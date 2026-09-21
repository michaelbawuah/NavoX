import { platform } from "@/lib/platform";

export default function Home() {
  return (
    <main>
      <p className="eyebrow">NavoX // Engineering Foundation</p>
      <h1>Operational awareness, built to earn trust.</h1>
      <p className="summary">
        The NavoX web foundation is online. Identity, Google connections, and AI are deliberately
        deferred until the tenant, policy, and durable-workflow layers are ready.
      </p>
      <section aria-label="Foundation status" className="status">
        <span className="status-dot" />
        <span>{platform.name} {platform.version} — Milestone 0</span>
      </section>
    </main>
  );
}

