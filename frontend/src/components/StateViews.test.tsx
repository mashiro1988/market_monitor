import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { ApiError } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "./StateViews";

test("renders loading state label", () => {
  render(<LoadingState label="正在读取" />);
  expect(screen.getByText("正在读取")).toBeInTheDocument();
});

test("renders empty state title", () => {
  render(<EmptyState title="没有数据" />);
  expect(screen.getByText("没有数据")).toBeInTheDocument();
});

test("renders unified api error", () => {
  render(<ErrorState error={new ApiError({ code: "X", message: "失败", details: {} })} />);
  expect(screen.getByText("X: 失败")).toBeInTheDocument();
});
