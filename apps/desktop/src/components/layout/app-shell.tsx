import { BookOpen, Library, Plus, Settings } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/", label: "Library", icon: Library, end: true },
  { to: "/new", label: "New Project", icon: Plus, end: false },
];

/**
 * Persistent chrome around every screen: a slim sidebar for the two
 * always-available destinations (Library, New Project) plus Settings, with
 * the active gate/project screens rendered through the route outlet. The
 * three gates are reached from a project card, not the sidebar, since they
 * only make sense in the context of one open project.
 */
export function AppShell() {
  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-950">
        <div className="flex items-center gap-2 px-4 py-4">
          <BookOpen className="size-5 text-neutral-500" aria-hidden />
          <span className="font-semibold tracking-tight">Adib</span>
        </div>

        <nav className="flex flex-1 flex-col gap-1 px-2">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-neutral-200/70 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-50"
                    : "text-neutral-600 hover:bg-neutral-200/40 dark:text-neutral-400 dark:hover:bg-neutral-900",
                )
              }
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="px-2 pb-3">
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-neutral-200/70 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-50"
                  : "text-neutral-600 hover:bg-neutral-200/40 dark:text-neutral-400 dark:hover:bg-neutral-900",
              )
            }
          >
            <Settings className="size-4" aria-hidden />
            Settings
          </NavLink>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto bg-white dark:bg-neutral-950">
        <Outlet />
      </main>
    </div>
  );
}
