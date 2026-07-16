export const OKX_GAPFILL_SOURCE = "okx_gapfill";

// 手工维护的 UI 配对关系；types.ts 由后端 schema 自动生成，不在生成文件里放常量。
export const PERP_PROXY_PAIRS: Readonly<Record<string, string>> = {
  "NQ=F": "QQQ-USDT-SWAP",
  "CL=F": "CL-USDT-SWAP",
  "GC=F": "XAU-USDT-SWAP"
};
