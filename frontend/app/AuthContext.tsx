"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

const API_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

const STORAGE_KEY = "mananabas_auth";

export type AuthUser = {
  id: number;
  email: string;
  name: string | null;
  picture_url: string | null;
};

type StoredAuth = {
  token: string;
  user: AuthUser;
};

// Shape returned by POST /api/auth/google (backend/app/schemas.py AuthResponse).
type AuthApiResponse = {
  access_token: string;
  user: AuthUser;
};

type AuthContextValue = {
  user: AuthUser | null;
  token: string | null;
  ready: boolean;
  authError: string | null;
  loginWithGoogleCredential: (credential: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  // Rehydrate from localStorage on mount so a page refresh doesn't sign
  // the user out.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed: StoredAuth = JSON.parse(raw);
        if (parsed?.token && parsed?.user) {
          setToken(parsed.token);
          setUser(parsed.user);
        }
      }
    } catch {
      // Corrupt/blocked localStorage — just start signed out.
    } finally {
      setReady(true);
    }
  }, []);

  const persist = (next: StoredAuth | null) => {
    try {
      if (next) {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } else {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // Ignore storage failures (e.g. private mode) — auth still works
      // for the current tab session via React state.
    }
  };

  const loginWithGoogleCredential = useCallback(async (credential: string) => {
    setAuthError(null);
    try {
      const res = await fetch(`${API_URL}/api/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential }),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const body = await res.json();
          if (typeof body === "object" && body && "detail" in body) {
            detail = String((body as { detail: unknown }).detail);
          }
        } catch {
          // response body wasn't JSON; keep statusText
        }
        throw new Error(`Sign-in failed: ${detail}`);
      }
      const data: AuthApiResponse = await res.json();
      setToken(data.access_token);
      setUser(data.user);
      persist({ token: data.access_token, user: data.user });
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    persist(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, token, ready, authError, loginWithGoogleCredential, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
