import { HashRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { EngineGate } from "@/components/layout/engine-gate";
import LibraryRoute from "@/routes/library";
import NewProjectRoute from "@/routes/new-project";
import Gate1Route from "@/routes/gate1";
import Gate2Route from "@/routes/gate2";
import Gate3Route from "@/routes/gate3";
import SettingsRoute from "@/routes/settings";

/**
 * `HashRouter` rather than `BrowserRouter`: the webview serves the built app
 * from a `tauri://` / `file://`-style origin with no server to handle deep
 * link paths, so hash-based routing is what avoids a blank page on refresh.
 */
export default function App() {
  return (
    <EngineGate>
      <HashRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<LibraryRoute />} />
            <Route path="new" element={<NewProjectRoute />} />
            <Route path="projects/:projectId/structure" element={<Gate1Route />} />
            <Route path="projects/:projectId/style" element={<Gate2Route />} />
            <Route path="projects/:projectId/review" element={<Gate3Route />} />
            <Route path="settings" element={<SettingsRoute />} />
          </Route>
        </Routes>
      </HashRouter>
    </EngineGate>
  );
}
