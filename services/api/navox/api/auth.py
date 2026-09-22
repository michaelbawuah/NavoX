from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings, get_settings
from navox.db.models import User, UserSession, Workspace, WorkspaceMembership
from navox.db.session import get_database_session

router = APIRouter(prefix="/auth", tags=["authentication"])
password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash("navox-invalid-password")

DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


class RegistrationRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    workspace_type: str


class AccountResponse(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str | None
    workspace: WorkspaceResponse


class ExtensionLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    account: AccountResponse


class CurrentAccount:
    def __init__(self, user: User, workspace: Workspace) -> None:
        self.user = user
        self.workspace = workspace


def normalized_email(email: EmailStr) -> str:
    return str(email).strip().casefold()


def personal_workspace_name(display_name: str) -> str:
    return f"{display_name.strip()}'s workspace"


def hash_session_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def is_secure_environment(settings: Settings) -> bool:
    return settings.app_environment.casefold() not in {"development", "test"}


def authentication_required() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
    )


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 60 * 60,
        httponly=True,
        secure=is_secure_environment(settings),
        samesite="lax",
        path="/",
    )


def account_response(user: User, workspace: Workspace) -> AccountResponse:
    return AccountResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        workspace=WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            workspace_type=workspace.workspace_type,
        ),
    )


async def create_session(
    database: AsyncSession,
    user_id: UUID,
    settings: Settings,
    *,
    client_type: str = "web",
    ttl_hours: int | None = None,
) -> tuple[str, datetime]:
    token = token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(
        hours=ttl_hours if ttl_hours is not None else settings.session_ttl_hours
    )
    database.add(
        UserSession(
            user_id=user_id,
            token_hash=hash_session_token(token),
            client_type=client_type,
            expires_at=expires_at,
        )
    )
    return token, expires_at


def request_session_token(request: Request, settings: Settings) -> str | None:
    authorization = request.headers.get("Authorization")
    if authorization is not None:
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token.strip():
            return None
        return token.strip()
    return request.cookies.get(settings.session_cookie_name)


def request_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not token.strip():
        return None
    return token.strip()


async def password_account(
    payload: LoginRequest,
    database: AsyncSession,
) -> tuple[User, Workspace]:
    email = normalized_email(payload.email)
    user = await database.scalar(select(User).where(User.email == email))
    if user is None or user.password_hash is None:
        password_hasher.verify(payload.password, dummy_password_hash)
        raise invalid_credentials()
    if not password_hasher.verify(payload.password, user.password_hash):
        raise invalid_credentials()

    workspace = await database.scalar(
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user.id, WorkspaceMembership.role == "owner")
        .order_by(Workspace.created_at)
    )
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No accessible workspace")
    return user, workspace


async def get_current_account(
    request: Request,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CurrentAccount:
    token = request_session_token(request, settings)
    if token is None:
        raise authentication_required()

    result = await database.execute(
        select(User, Workspace)
        .join(UserSession, UserSession.user_id == User.id)
        .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            UserSession.token_hash == hash_session_token(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
            WorkspaceMembership.role == "owner",
        )
        .order_by(Workspace.created_at)
    )
    row = result.first()
    if row is None:
        raise authentication_required()
    return CurrentAccount(user=row[0], workspace=row[1])


CurrentAccountDependency = Annotated[CurrentAccount, Depends(get_current_account)]


@router.post("/register", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegistrationRequest,
    response: Response,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AccountResponse:
    email = normalized_email(payload.email)
    display_name = payload.display_name.strip()
    token = token_urlsafe(32)

    try:
        async with database.begin():
            user = User(
                email=email,
                display_name=display_name,
                password_hash=password_hasher.hash(payload.password),
            )
            database.add(user)
            await database.flush()

            workspace = Workspace(
                name=personal_workspace_name(display_name),
                workspace_type="personal",
            )
            database.add(workspace)
            await database.flush()

            database.add(
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="owner",
                )
            )
            database.add(
                UserSession(
                    user_id=user.id,
                    token_hash=hash_session_token(token),
                    expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
                )
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        ) from error

    set_session_cookie(response, token, settings)
    return account_response(user, workspace)


@router.post("/login", response_model=AccountResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AccountResponse:
    user, workspace = await password_account(payload, database)
    token, _expires_at = await create_session(database, user.id, settings)
    await database.commit()
    set_session_cookie(response, token, settings)
    return account_response(user, workspace)


@router.get("/me", response_model=AccountResponse)
async def me(current_account: CurrentAccountDependency) -> AccountResponse:
    return account_response(current_account.user, current_account.workspace)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    token = request.cookies.get(settings.session_cookie_name)
    if token is not None:
        await database.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_session_token(token))
            .values(revoked_at=datetime.now(UTC))
        )
        await database.commit()
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"status": "signed_out"}


@router.post("/extension/login", response_model=ExtensionLoginResponse)
async def extension_login(
    payload: LoginRequest,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ExtensionLoginResponse:
    user, workspace = await password_account(payload, database)
    token, expires_at = await create_session(
        database,
        user.id,
        settings,
        client_type="extension",
        ttl_hours=settings.extension_session_ttl_hours,
    )
    await database.commit()
    return ExtensionLoginResponse(
        access_token=token,
        expires_at=expires_at,
        account=account_response(user, workspace),
    )


@router.post("/extension/logout")
async def extension_logout(
    request: Request,
    database: DatabaseSession,
) -> dict[str, str]:
    token = request_bearer_token(request)
    if token is None:
        raise authentication_required()
    session = await database.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_session_token(token),
            UserSession.client_type == "extension",
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )
    if session is None:
        raise authentication_required()
    session.revoked_at = datetime.now(UTC)
    await database.commit()
    return {"status": "signed_out"}
