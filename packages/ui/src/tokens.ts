export const courseColors = [
	"#4FD1B5",
	"#7AA2F7",
	"#B69CFF",
	"#F5B14C",
	"#FF8A9B",
	"#5BD38C",
	"#FF8A5B",
	"#67D4F0",
] as const;
/** Stable per course even when a list is reordered or filtered. */
export function courseColor(id: string): string {
	let hash = 0;
	for (const character of id)
		hash = (Math.imul(hash, 31) + character.charCodeAt(0)) >>> 0;
	return courseColors[hash % courseColors.length];
}
