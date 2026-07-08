import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { AppShell } from "./AppShell";

test("renders navigation items", () => {
  const router = createMemoryRouter([{ path: "/", element: <AppShell /> }], { initialEntries: ["/"] });
  render(<RouterProvider router={router} />);
  expect(screen.getByText("市场概览")).toBeInTheDocument();
  expect(screen.getByText("新闻快讯")).toBeInTheDocument();
  expect(screen.queryByText("链上数据")).not.toBeInTheDocument();
});
