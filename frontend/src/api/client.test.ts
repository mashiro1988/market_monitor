import { describe, expect, it } from "vitest";
import { ApiError, apiErrorText } from "./client";

describe("apiErrorText(修复研究页动作静默失败,2026-08-09 诊断)", () => {
  it("ApiError 优先显示后端 message(如 SUGGEST_FAILED 的具体原因)", () => {
    const err = new ApiError({
      code: "SUGGEST_FAILED",
      message: "DeepSeek 关键词建议返回空 content",
      details: {},
    });
    expect(apiErrorText(err, "AI 建议失败")).toBe("DeepSeek 关键词建议返回空 content");
  });

  it("后端 message 为空时退回兜底文案", () => {
    const err = new ApiError({ code: "X", message: "", details: {} });
    expect(apiErrorText(err, "AI 建议失败")).toBe("AI 建议失败");
  });

  it("非 ApiError(断网/超时等)也退回兜底文案,不裸奔堆栈", () => {
    expect(apiErrorText(new Error("fetch failed"), "挂接失败")).toBe("挂接失败");
    expect(apiErrorText(undefined, "操作失败")).toBe("操作失败");
  });
});
