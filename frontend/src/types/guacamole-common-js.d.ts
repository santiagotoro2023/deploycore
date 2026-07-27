// Minimal ambient declaration for the one corner of guacamole-common-js's
// API this app actually uses (see RemoteSession.tsx's Connect-mode path) -
// the package ships no types of its own and there's no need to pull in
// (or hand-write) a declaration for its full surface for that.
declare module "guacamole-common-js" {
  namespace Guacamole {
    class WebSocketTunnel {
      constructor(url: string);
      // Guacamole.Client only ever wires the tunnel's oninstruction and fires
      // client.onerror for in-protocol "error" instructions - it never chains
      // tunnel-level failures (handshake refusal, a parser error, the 15s
      // receive timeout). Those only reach tunnel.onerror, so it must be wired
      // directly or every tunnel failure is swallowed and the UI hangs.
      onerror: ((status: { message?: string; code?: number }) => void) | null;
      onstatechange: ((state: number) => void) | null;
    }

    class Client {
      constructor(tunnel: WebSocketTunnel);
      connect(data?: string): void;
      disconnect(): void;
      getDisplay(): Display;
      sendSize(width: number, height: number): void;
      sendMouseState(state: MouseState): void;
      sendKeyEvent(pressed: 0 | 1, keysym: number): void;
      createClipboardStream(mimetype: string): OutputStream;
      onerror: ((status: { message?: string; code?: number }) => void) | null;
      onstatechange: ((state: number) => void) | null;
      onclipboard: ((stream: InputStream, mimetype: string) => void) | null;
    }

    class InputStream {
      onblob: ((data: string) => void) | null;
      onend: (() => void) | null;
    }

    class OutputStream {
      onack: ((status: { code: number }) => void) | null;
    }

    class StringReader {
      constructor(stream: InputStream);
      ontext: ((text: string) => void) | null;
      onend: (() => void) | null;
    }

    class StringWriter {
      constructor(stream: OutputStream);
      sendText(text: string): void;
      sendEnd(): void;
    }

    class Display {
      getElement(): HTMLElement;
      scale(scale: number): void;
      getWidth(): number;
      getHeight(): number;
    }

    class Mouse {
      constructor(element: HTMLElement);
      onmousedown: ((state: MouseState) => void) | null;
      onmouseup: ((state: MouseState) => void) | null;
      onmousemove: ((state: MouseState) => void) | null;
    }

    interface MouseState {
      x: number;
      y: number;
      left: boolean;
      middle: boolean;
      right: boolean;
      up: boolean;
      down: boolean;
    }

    class Keyboard {
      constructor(element: HTMLElement | Document);
      onkeydown: ((keysym: number) => void) | null;
      onkeyup: ((keysym: number) => void) | null;
      // Releases every key Guacamole currently believes is held - called on
      // teardown so navigating away mid-keypress can't strand a modifier.
      reset(): void;
    }
  }
  export = Guacamole;
}
