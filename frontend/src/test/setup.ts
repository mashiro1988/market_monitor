import "@testing-library/jest-dom/vitest";

// recharts 的 ResponsiveContainer 依赖 ResizeObserver，jsdom 不提供。
// 测试不验证图表 SVG 尺寸（只验证非图表 DOM 与纯函数逻辑），空实现即可。
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}
