import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div>
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm text-slate-600">
        This page does not exist or has not been built yet.{" "}
        <Link to="/" className="underline">
          Back to overview
        </Link>
      </p>
    </div>
  );
}
