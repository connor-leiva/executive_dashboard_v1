/* Spring · Command Center — shared brand tokens + formatting helpers.
   Palette + type from Spring's Visual Identity System:
   Evergreen / Parchment / Poppy, Poppins + Inter, ribbed gradient.
   Copied verbatim from the canonical mockup. */

export const T = {
  evergreen: "#002E2C",
  meadow: "#61835E",
  sprout: "#B8CCB8",
  parchment: "#F8F5F2",
  line: "#E8E0D4",
  white: "#FFFFFF",
  petal: "#FFBA9F",
  poppy: "#FA8069",
  poppyText: "#CE4E29",
  mist: "#DCE7E9",
  teal: "#227175",
  daffodil: "#FFF3AD",
  ink: "#002E2C",
  slate: "#56655C",
  muted: "#8A968C",
  onDark: "#F3EEE7",
  onDarkMute: "#9CB0AB",
};

export const STATUS = {
  healthy: { dot: T.meadow, text: "#4F6A4D", label: "Healthy" },
  watch: { dot: T.poppy, text: T.poppyText, label: "Watch" },
  opportunity: { dot: T.teal, text: T.teal, label: "Opportunity" },
};

export const usd = (n) => "$" + Math.abs(Math.round(n)).toLocaleString("en-US");
export const signed = (n) => (n < 0 ? `(${usd(n)})` : usd(n));
