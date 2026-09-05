import { Dashboard } from "@/components/Dashboard";

/**
 * The dashboard is a client component behind a server page.
 *
 * Everything on it is live — polled job progress, ETA countdowns, optimistic
 * toggles — so server-rendering the account list would only produce markup that is
 * replaced on hydration. The server page stays as the routing and metadata entry
 * point.
 */
export default function HomePage() {
  return <Dashboard />;
}
