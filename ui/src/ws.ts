/** The page's link to the conductor: one authenticated socket and the upload route. */

import {
  errorText,
  parseServerEvent,
  type ClientMessage,
  type ServerEvent,
} from "./state";

export const PROTOCOL_PREFIX = "ghostband.";
export const TOKEN_HEADER = "X-Ghostband-Token";

/** The conductor prints the page address with its token in `t`. */
export function pageToken(search: string): string | null {
  const token = new URLSearchParams(search).get("t");
  return token === null || token === "" ? null : token;
}

export function socketUrl(location: {
  readonly protocol: string;
  readonly host: string;
}): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws`;
}

export function decodeEvent(data: unknown): ServerEvent | null {
  if (typeof data !== "string") {
    return null;
  }
  try {
    return parseServerEvent(JSON.parse(data));
  } catch {
    return null;
  }
}

export interface Connection {
  send(message: ClientMessage): void;
  close(): void;
}

export interface ConnectionHandlers {
  event(event: ServerEvent): void;
  closed(): void;
}

/** Hold messages sent before the socket opens instead of losing them. */
export function connect(
  url: string,
  token: string,
  handlers: ConnectionHandlers,
): Connection {
  const socket = new WebSocket(url, [PROTOCOL_PREFIX + token]);
  const pending: string[] = [];
  socket.addEventListener("open", () => {
    for (const data of pending.splice(0)) {
      socket.send(data);
    }
  });
  socket.addEventListener("message", (message: MessageEvent<unknown>) => {
    const event = decodeEvent(message.data);
    if (event !== null) {
      handlers.event(event);
    }
  });
  socket.addEventListener("close", () => {
    handlers.closed();
  });
  return {
    send(message) {
      const data = JSON.stringify(message);
      if (socket.readyState === WebSocket.CONNECTING) {
        pending.push(data);
      } else if (socket.readyState === WebSocket.OPEN) {
        socket.send(data);
      }
    },
    close() {
      socket.close();
    },
  };
}

/** Upload one file privately; the conductor answers with an opaque id. */
export async function uploadFile(file: File, token: string): Promise<string> {
  const body = new FormData();
  body.append("file", file, file.name);
  const response = await fetch("/api/upload", {
    method: "POST",
    headers: { [TOKEN_HEADER]: token },
    body,
  });
  const reply: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const event = parseServerEvent(reply);
    throw new Error(
      event?.event === "error"
        ? errorText(event)
        : `Upload failed with HTTP status ${String(response.status)}.`,
    );
  }
  if (
    typeof reply === "object" &&
    reply !== null &&
    "upload_id" in reply &&
    typeof reply.upload_id === "string"
  ) {
    return reply.upload_id;
  }
  throw new Error("Upload reply has no upload id.");
}
