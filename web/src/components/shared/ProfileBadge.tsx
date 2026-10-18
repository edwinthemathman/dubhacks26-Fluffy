import type { ProfileId } from "../../stream/events";
import { profileLabel } from "../../stream/format";

export function ProfileBadge({ profile }: { profile: ProfileId | null }) {
  return <span className="pill">{profileLabel(profile)}</span>;
}
