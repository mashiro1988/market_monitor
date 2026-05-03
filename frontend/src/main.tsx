import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { AlertsPage } from "./pages/AlertsPage";
import { AnnotationsPage } from "./pages/AnnotationsPage";
import { MarketPage } from "./pages/MarketPage";
import { NewsPage } from "./pages/NewsPage";
import { OnchainPage } from "./pages/OnchainPage";
import { PredictionsPage } from "./pages/PredictionsPage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1
    }
  }
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/market" replace /> },
      { path: "market", element: <MarketPage /> },
      { path: "news", element: <NewsPage /> },
      { path: "predictions", element: <PredictionsPage /> },
      { path: "alerts", element: <AlertsPage /> },
      { path: "annotations", element: <AnnotationsPage /> },
      { path: "onchain", element: <OnchainPage /> }
    ]
  }
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>
);
