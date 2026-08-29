import { randomUUID } from "node:crypto";
import {
  chmodSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

type SessionState = "idle" | "running" | "waiting";

interface UsageStats {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  cost: number;
  subagentCost: number;
  totalTokens: number;
}

interface ContextStats {
  tokens: number | null;
  contextWindow: number;
  percent: number | null;
}

interface LiveSessionV1 {
  version: 1;
  pid: number;
  parentPid: number;
  instanceId: string;
  processStartTime: string;
  sessionId: string;
  sessionFile: string | undefined;
  sessionName: string;
  displayName: string;
  titleSource: string;
  cwd: string;
  project: string;
  state: SessionState;
  detail: string;
  prompt: string;
  model: string;
  thinking: string;
  usage: UsageStats;
  context: ContextStats | null;
  startedAt: string | number;
  lastMessageAt: string | number | null;
  updatedAt: number;
}

const currentUid = process.getuid?.();
const configuredRuntimeRoot = process.env.PI_BEACON_PATHS__RUNTIME_DIR;
const runtimeBase =
  process.env.XDG_RUNTIME_DIR ?? join(tmpdir(), `pi-runtime-${currentUid ?? "user"}`);
const runtimeRoot = configuredRuntimeRoot ?? join(runtimeBase, "pi-beacon");
const statusDir = join(runtimeRoot, "sessions");
const statusPath = join(statusDir, `${process.pid}.json`);
const instanceId = randomUUID();

function ensurePrivateDirectory(path: string): void {
  if (currentUid === undefined) {
    throw new Error("Pi Beacon requires a Unix user identity");
  }
  mkdirSync(path, { recursive: true, mode: 0o700 });
  const metadata = lstatSync(path);
  if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
    throw new Error(`Pi Beacon runtime path is not a private directory: ${path}`);
  }
  if (metadata.uid !== currentUid) {
    throw new Error(`Pi Beacon runtime path has a foreign owner: ${path}`);
  }
  chmodSync(path, 0o700);
}

function ensureRuntimeDirectories(): void {
  if (!configuredRuntimeRoot) ensurePrivateDirectory(runtimeBase);
  ensurePrivateDirectory(runtimeRoot);
  ensurePrivateDirectory(statusDir);
}

function processStartTime(pid: number): string {
  try {
    const payload = readFileSync(`/proc/${pid}/stat`, "utf8");
    const fields = payload.slice(payload.lastIndexOf(")") + 2).split(/\s+/);
    return fields[19] ?? "";
  } catch {
    return "";
  }
}

const currentProcessStartTime = processStartTime(process.pid);
const backendExecutable =
  process.env.PI_BEACON_EXECUTABLE ?? join(process.env.HOME ?? "", ".local", "bin", "pi-beacon");

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function emptyUsage(): UsageStats {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    cost: 0,
    subagentCost: 0,
    totalTokens: 0,
  };
}

function subagentCost(entry: Record<string, unknown>): number {
  if (entry.type !== "message" || !isRecord(entry.message)) return 0;
  const message = entry.message;
  if (message.role !== "toolResult" || message.toolName !== "subagent") return 0;
  if (!isRecord(message.details) || !Array.isArray(message.details.results)) return 0;
  let total = 0;
  for (const result of message.details.results) {
    if (!isRecord(result) || !isRecord(result.usage)) continue;
    if (typeof result.usage.cost === "number") total += result.usage.cost;
  }
  return total;
}

function computeUsage(entries: readonly unknown[]): UsageStats {
  const stats = emptyUsage();
  for (const entry of entries) {
    if (!isRecord(entry)) continue;
    let usage: Record<string, unknown> | undefined;
    if (entry.type === "message" && isRecord(entry.message)) {
      const role = entry.message.role;
      if ((role === "assistant" || role === "toolResult") && isRecord(entry.message.usage)) {
        usage = entry.message.usage;
      }
    } else if (
      (entry.type === "compaction" || entry.type === "branch_summary") &&
      isRecord(entry.usage)
    ) {
      usage = entry.usage;
    }

    if (!usage) {
      stats.subagentCost += subagentCost(entry);
      continue;
    }
    const input = typeof usage.input === "number" ? usage.input : 0;
    const output = typeof usage.output === "number" ? usage.output : 0;
    const cacheRead = typeof usage.cacheRead === "number" ? usage.cacheRead : 0;
    const cacheWrite = typeof usage.cacheWrite === "number" ? usage.cacheWrite : 0;
    const totalTokens =
      typeof usage.totalTokens === "number"
        ? usage.totalTokens
        : input + output + cacheRead + cacheWrite;
    stats.input += input;
    stats.output += output;
    stats.cacheRead += cacheRead;
    stats.cacheWrite += cacheWrite;
    stats.totalTokens += totalTokens;
    if (isRecord(usage.cost) && typeof usage.cost.total === "number") {
      stats.cost += usage.cost.total;
    }
  }
  return stats;
}

function readContextStats(ctx: ExtensionContext): ContextStats | null {
  const usage = ctx.getContextUsage();
  if (!isRecord(usage) || typeof usage.contextWindow !== "number") return null;
  const tokens = typeof usage.tokens === "number" ? usage.tokens : null;
  const percent =
    typeof usage.percent === "number"
      ? usage.percent
      : tokens === null
        ? null
        : (tokens / usage.contextWindow) * 100;
  return { tokens, contextWindow: usage.contextWindow, percent };
}

function removeStatus(): void {
  try {
    unlinkSync(statusPath);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      console.error("pi desktop status cleanup failed", error);
    }
  }
}

function cleanupStaleStatuses(): void {
  mkdirSync(statusDir, { recursive: true, mode: 0o700 });
  for (const fileName of readdirSync(statusDir)) {
    if (!fileName.endsWith(".json")) continue;
    const path = join(statusDir, fileName);
    try {
      const payload = JSON.parse(readFileSync(path, "utf8")) as {
        pid?: number;
        processStartTime?: string;
      };
      if (payload.pid === process.pid) continue;
      if (
        payload.pid &&
        payload.processStartTime &&
        processStartTime(payload.pid) === payload.processStartTime
      ) {
        continue;
      }
    } catch {
      // Invalid files are removed below.
    }
    try {
      unlinkSync(path);
    } catch {
      // A concurrent Pi process may have replaced or removed the file.
    }
  }
}

function messageText(value: unknown): string {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return "";
  return value
    .filter(isRecord)
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => String(block.text))
    .join(" ");
}

function derivedTitle(value: string): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  if (normalized.length <= 56) return normalized;
  const clipped = normalized.slice(0, 56);
  const boundary = clipped.lastIndexOf(" ");
  return `${clipped.slice(0, boundary > 32 ? boundary : 56).trim()}…`;
}

function entryTimestamp(value: unknown): string | number | null {
  if (!isRecord(value)) return null;
  return typeof value.timestamp === "string" || typeof value.timestamp === "number"
    ? value.timestamp
    : null;
}

export default function (pi: ExtensionAPI) {
  let state: SessionState = "idle";
  let detail = "Ready";
  let currentPrompt = "";
  let sessionStartedAt: string | number = Date.now();
  let lastMessageAt: string | number | null = null;
  let sessionId = `pid-${process.pid}`;
  let displayName = "";
  let titleSource = "project";
  let usage = emptyUsage();
  let lastStreamingWrite = 0;
  let runStartedAt = 0;
  let runFailed = false;

  function refreshUsage(ctx: ExtensionContext): void {
    usage = computeUsage(ctx.sessionManager.getEntries());
  }

  function refreshMetadata(ctx: ExtensionContext): void {
    const branch = ctx.sessionManager.getBranch();
    const explicitName = (pi.getSessionName() ?? "").trim();
    let firstPrompt = "";
    for (const entry of branch) {
      if (!isRecord(entry) || entry.type !== "message" || !isRecord(entry.message)) continue;
      if (entry.message.role === "user") {
        firstPrompt = messageText(entry.message.content);
        break;
      }
    }
    displayName = explicitName || derivedTitle(firstPrompt) || basename(ctx.cwd) || ctx.cwd;
    titleSource = explicitName ? "explicit" : firstPrompt ? "prompt" : "project";
    sessionId = ctx.sessionManager.getSessionId();
    sessionStartedAt = entryTimestamp(ctx.sessionManager.getHeader()) ?? sessionStartedAt;
    for (let index = branch.length - 1; index >= 0; index -= 1) {
      const entry = branch[index];
      if (!isRecord(entry) || entry.type !== "message") continue;
      lastMessageAt = entryTimestamp(entry);
      break;
    }
  }

  function emitNotification(
    event: "settled" | "waiting" | "error",
    duration = 0,
    notificationDetail = "",
  ): void {
    const project = displayName || "Pi session";
    void pi
      .exec(
        backendExecutable,
        [
          "notify",
          event,
          "--project",
          project,
          "--detail",
          notificationDetail,
          "--duration",
          String(duration),
        ],
        { timeout: 5000 },
      )
      .catch(() => undefined);
  }

  function writeStatus(ctx: ExtensionContext): void {
    const sessionFile = ctx.sessionManager.getSessionFile();
    const payload = {
      version: 1,
      pid: process.pid,
      parentPid: process.ppid,
      instanceId,
      processStartTime: currentProcessStartTime,
      sessionId,
      sessionFile,
      sessionName: pi.getSessionName() ?? "",
      displayName,
      titleSource,
      cwd: ctx.cwd,
      project: basename(ctx.cwd) || ctx.cwd,
      state,
      detail,
      prompt: currentPrompt,
      model: ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : "",
      thinking: ctx.thinkingLevel ?? "",
      usage: {
        ...usage,
        cost: usage.cost + usage.subagentCost,
      },
      context: readContextStats(ctx),
      startedAt: sessionStartedAt,
      lastMessageAt,
      updatedAt: Date.now(),
    } satisfies LiveSessionV1;
    const temporaryPath = `${statusPath}.${process.pid}.tmp`;
    writeFileSync(temporaryPath, `${JSON.stringify(payload)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    renameSync(temporaryPath, statusPath);
  }

  pi.on("session_start", (_event, ctx) => {
    ensureRuntimeDirectories();
    cleanupStaleStatuses();
    sessionStartedAt = Date.now();
    state = "idle";
    detail = "Ready";
    currentPrompt = "";
    refreshMetadata(ctx);
    refreshUsage(ctx);
    writeStatus(ctx);
  });

  pi.on("before_agent_start", (event, ctx) => {
    state = "running";
    detail = "Starting";
    runStartedAt = Date.now();
    runFailed = false;
    currentPrompt = String(event.prompt ?? "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 160);
    refreshMetadata(ctx);
    lastMessageAt = Date.now();
    writeStatus(ctx);
  });

  pi.on("agent_start", (_event, ctx) => {
    state = "running";
    detail = "Thinking";
    writeStatus(ctx);
  });

  pi.on("turn_start", (_event, ctx) => {
    state = "running";
    detail = "Thinking";
    writeStatus(ctx);
  });

  pi.on("message_update", (_event, ctx) => {
    const now = Date.now();
    if (now - lastStreamingWrite < 1000) return;
    lastStreamingWrite = now;
    refreshUsage(ctx);
    writeStatus(ctx);
  });

  pi.on("tool_execution_start", (event, ctx) => {
    state = "running";
    detail = `Tool: ${event.toolName}`;
    writeStatus(ctx);
  });

  pi.on("tool_execution_end", (_event, ctx) => {
    state = "running";
    detail = "Thinking";
    writeStatus(ctx);
  });

  pi.on("ui_prompt_start", (event, ctx) => {
    state = "waiting";
    detail = event.title ? `Waiting: ${event.title}` : `Waiting: ${event.kind}`;
    writeStatus(ctx);
    emitNotification("waiting", 0, detail);
  });

  pi.on("ui_prompt_end", (_event, ctx) => {
    state = "running";
    detail = "Resuming";
    writeStatus(ctx);
  });

  pi.on("agent_end", (event) => {
    const failed = event.messages.some(
      (message) =>
        isRecord(message) && message.role === "assistant" && message.stopReason === "error",
    );
    if (!failed) return;
    runFailed = true;
    emitNotification("error", 0, "Pi stopped with an error. Open the session for details.");
  });

  pi.on("agent_settled", (_event, ctx) => {
    state = "idle";
    detail = "Ready";
    refreshMetadata(ctx);
    refreshUsage(ctx);
    writeStatus(ctx);
    const duration = runStartedAt ? (Date.now() - runStartedAt) / 1000 : 0;
    if (!runFailed) emitNotification("settled", duration);
    runStartedAt = 0;
    runFailed = false;
  });

  pi.on("session_info_changed", (_event, ctx) => {
    refreshMetadata(ctx);
    writeStatus(ctx);
  });
  pi.on("model_select", (_event, ctx) => writeStatus(ctx));
  pi.on("thinking_level_select", (_event, ctx) => writeStatus(ctx));
  pi.on("session_shutdown", removeStatus);
}
