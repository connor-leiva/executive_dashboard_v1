/* The one money figure on the Allocations page (UI spec 8.1).
 *
 * There were four of these, one per view, and three of them were right. The fourth sat inside
 * a wrapper span that nothing right-aligned, so on the Rules ledger every figure rendered a
 * column to the left of its own header: a retained 1,666.67 read as 1,666.67 sent to partners
 * (U1). A component that can be misaligned by its wrapper will be, eventually, in whichever
 * view is written last.
 *
 * DISPLAY COMES FROM THE STYLESHEET, not from an inline style, and that is deliberate. An
 * inline style beats a stylesheet rule on specificity, so an inline `display: block` here
 * would defeat the `display: none` media queries that drop these columns at 820px and 520px
 * -- the exact failure the grid-template comment in BooksAllocations.jsx was written about.
 * `.al-money` is defined in that style block.
 */
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";

export const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums",
                     whiteSpace: "nowrap" };

/** `v` in ledger form: two places, thousands separated, negatives in parens.
 *
 * Zero is muted at weight 400 on purpose. Most rows on this page are zero in any one month,
 * and a ledger of bold zeros hides the handful of rows that are not.
 */
export default function Money({ v, tone, size, weight, className = "", title }) {
  const n = Number(v || 0);
  const body = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2,
                                                       maximumFractionDigits: 2 });
  const zero = n === 0;
  return (
    <span className={`al-money ${className}`.trim()} title={title}
          style={{ ...NUM,
                   fontSize: size || undefined,
                   fontWeight: zero ? 400 : (weight || undefined),
                   color: tone || (zero ? L.muted : L.body) }}>
      {n < 0 ? `(${body})` : body}
    </span>
  );
}
