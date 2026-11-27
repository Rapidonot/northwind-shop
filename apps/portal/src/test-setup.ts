import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { resetSessionOrder } from "./session";

// SA-01: each test is a fresh page, so its first session-path request goes alone (session.ts).
afterEach(resetSessionOrder);
