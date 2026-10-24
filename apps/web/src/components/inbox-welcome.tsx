"use client";
import Link from "next/link";
import styles from "./inbox-welcome.module.css";
import { NoaArtwork } from "./noa-artwork";

export function InboxWelcome() {
  return (
    <section className={styles.welcome}>
      <header className={styles.hero}>
        <div className={styles.noa}>
          <span>i got you</span>
          <NoaArtwork size={180} mood="happy" />
        </div>
        <div>
          <h2>Never miss an email that needs you</h2>
          <p>
            Connect your school email. NavoX finds the messages that need a
            reply, like a professor’s question or a deadline change, and helps
            you answer them.
          </p>
          <div className={styles.connect}>
            <Link
              className="nx-button"
              data-variant="primary"
              href="/connections?source=gmail"
            >
              Connect Gmail
            </Link>
            <Link
              className="nx-button"
              href="/connections?source=outlook#outlook"
            >
              Connect Outlook
            </Link>
          </div>
        </div>
      </header>
      <div className={styles.promises}>
        <article>
          <span aria-hidden="true">⌕</span>
          <h3>Finds what needs you</h3>
          <p>
            Questions from professors and TAs, deadline changes and requests,
            brought together for your review.
          </p>
        </article>
        <article>
          <span aria-hidden="true">✎</span>
          <h3>Drafts with you</h3>
          <p>
            NavoX helps prepare a reply from your schedule and what you tell it.
            You edit it, then decide.
          </p>
        </article>
        <article>
          <span aria-hidden="true">◇</span>
          <h3>You approve every send</h3>
          <p>
            Review the exact message and recipient before sending. You can
            disconnect anytime in Connections.
          </p>
        </article>
      </div>
      <section
        className={styles.examples}
        aria-label="Example emails, not your inbox"
      >
        <h3>
          What you’ll see <small>Example only</small>
        </h3>
        <div>
          <article>
            <span className={styles.avatar}>L</span>
            <div>
              <strong>Prof. Lee · Office hours this week</strong>
              <p>
                Moves office hours to Friday and asks students to reply if it
                doesn’t work
              </p>
            </div>
            <span className={styles.badge}>Reply before Friday</span>
            <span className={styles.draft}>Draft reply</span>
          </article>
          <article>
            <span className={styles.avatar}>R</span>
            <div>
              <strong>Registrar · Add/drop deadline</strong>
              <p>Last day to drop a course without a W is next Friday</p>
            </div>
            <span className={styles.badge}>Due soon</span>
          </article>
        </div>
      </section>
    </section>
  );
}
