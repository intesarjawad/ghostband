import { expect, test } from "vitest";
import { decodeEvent, pageToken, socketUrl } from "../src/ws";

test("the token comes from the t parameter the conductor prints", () => {
  expect(pageToken("?t=abc-123_x")).toBe("abc-123_x");
  expect(pageToken("?other=1&t=secret")).toBe("secret");
  expect(pageToken("")).toBeNull();
  expect(pageToken("?t=")).toBeNull();
});

test("the socket uses the page host and a matching scheme", () => {
  expect(socketUrl({ protocol: "http:", host: "127.0.0.1:7777" })).toBe(
    "ws://127.0.0.1:7777/ws",
  );
  expect(socketUrl({ protocol: "https:", host: "localhost:7777" })).toBe(
    "wss://localhost:7777/ws",
  );
});

test("only well-formed text frames decode", () => {
  expect(decodeEvent('{"event":"clock","audible_beat":2,"now_s":1}')).toEqual({
    event: "clock",
    audible_beat: 2,
    now_s: 1,
  });
  expect(decodeEvent("not json")).toBeNull();
  expect(decodeEvent('{"event":"clock"}')).toBeNull();
  expect(decodeEvent(new ArrayBuffer(4))).toBeNull();
});
