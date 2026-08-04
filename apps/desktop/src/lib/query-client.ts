import { QueryClient } from "@tanstack/react-query";

/**
 * One client for the whole app. Queries default to a short stale time rather
 * than 0: most screens here are wizards a user steps through in one sitting,
 * so refetching on every focus/mount would just be noise against a local
 * sidecar that isn't going to have concurrent external writers.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
    mutations: {
      retry: 0,
    },
  },
});
