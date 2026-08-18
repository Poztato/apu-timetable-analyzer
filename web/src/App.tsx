import { useEffect, useState } from "react";

import { BrandIcon } from "./BrandIcon";
import { CampusNotebook } from "./CampusNotebook";
import { Dashboard } from "./Dashboard";
import { loadDashboardData } from "./data";
import type { DashboardData } from "./types";

export { Dashboard } from "./Dashboard";
export { scheduleBlocksWithGaps } from "./VerticalTimetable";

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"wizard" | "dashboard">("wizard");

  useEffect(() => {
    const controller = new AbortController();
    loadDashboardData(controller.signal)
      .then(setData)
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(
          caught instanceof Error ? caught.message : "Unknown loading error.",
        );
      });
    return () => controller.abort();
  }, []);

  if (error) {
    return (
      <main className="load-state error-state">
        <span aria-hidden="true">!</span>
        <h1>Could not load the timetable data.</h1>
        <p>{error}</p>
        <p>Generate the static data, then reload this page.</p>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="load-state" aria-live="polite">
        <BrandIcon className="load-mark" />
        <h1>Preparing the latest timetables.</h1>
        <p>The latest scoring data is being read in your browser.</p>
        <div className="load-lines" aria-hidden="true"><i /><i /><i /></div>
      </main>
    );
  }

  if (view === "dashboard") {
    return (
      <Dashboard
        data={data}
        onBack={() => {
          setView("wizard");
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
    );
  }

  return (
    <CampusNotebook
      data={data}
      onOpenDashboard={() => {
        setView("dashboard");
        window.scrollTo({ top: 0, behavior: "smooth" });
      }}
    />
  );
}
