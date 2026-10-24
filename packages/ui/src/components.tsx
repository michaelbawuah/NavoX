import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";
export function Button({
	variant = "secondary",
	className = "",
	...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
	variant?: "primary" | "secondary" | "quiet";
}) {
	return (
		<button
			type="button"
			{...props}
			className={`nx-button ${className}`}
			data-variant={variant}
		/>
	);
}
export function Card({
	children,
	className = "",
}: {
	children: ReactNode;
	className?: string;
}) {
	return <div className={`nx-card ${className}`}>{children}</div>;
}
export function ListRow({
	title,
	detail,
	trailing,
	color,
	href,
}: {
	title: string;
	detail?: ReactNode;
	trailing?: ReactNode;
	color?: string;
	href?: string;
}) {
	const content = (
		<>
			<span
				className="nx-dot"
				style={{ "--nx-course": color } as CSSProperties}
			/>
			<span className="nx-row-body">
				<strong className="nx-row-title">{title}</strong>
				{detail && <span className="nx-row-detail">{detail}</span>}
			</span>
			{trailing && <span className="nx-row-trailing">{trailing}</span>}
		</>
	);
	return href ? (
		<a className="nx-row" href={href}>
			{content}
		</a>
	) : (
		<div className="nx-row">{content}</div>
	);
}
export function Chip({ children }: { children: ReactNode }) {
	return <span className="nx-chip">{children}</span>;
}
export function EmptyState({
	icon,
	children,
	action,
}: {
	icon: ReactNode;
	children: ReactNode;
	action: ReactNode;
}) {
	return (
		<div>
			<span aria-hidden="true">{icon}</span>
			<p>{children}</p>
			{action}
		</div>
	);
}
export function Toast({
	children,
	onUndo,
}: {
	children: ReactNode;
	onUndo?: () => void;
}) {
	return (
		<div className="nx-toast" role="status">
			{children}
			{onUndo && (
				<Button variant="quiet" onClick={onUndo}>
					Undo
				</Button>
			)}
		</div>
	);
}
