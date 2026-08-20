import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./index.css";
import "./wizard.css";
import "./dashboard.css";
import "./scoring-help.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Cannot find the dashboard root element.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
