/** The live room does not reduce its own event stream. See projectHomeToRoom. */
export function roomReducer(): never {
  throw new Error("Room state is a projection of the shared session.");
}
