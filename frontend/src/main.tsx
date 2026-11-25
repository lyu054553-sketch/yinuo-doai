import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/app.css";
import "./styles/compat.css";
import "./styles/resources.css";
import "./styles/v2.css";
import "./styles/v3.css";
import "./styles/v4.css";
import "./styles/glass.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);