import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "normalize.css";
import "@blueprintjs/core/lib/css/blueprint.css";
import "./layout.css";
import { App } from "./app/App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
