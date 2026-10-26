/** Shown on data screens while the portal holds demonstration data only. */
export default function DemoNotice() {
  return (
    <div role="note" className="rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <strong className="font-semibold">Development / demonstration environment.</strong> Institution and bank
      names are public names used for demonstration only. Nothing shown here is a verified record.
    </div>
  );
}
