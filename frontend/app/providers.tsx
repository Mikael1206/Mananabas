"use client";

import { GoogleOAuthProvider } from "@react-oauth/google";

import { AuthProvider } from "./AuthContext";

// Google Identity Services needs a client-side provider, so this stays out
// of layout.tsx (a server component) and wraps children here instead.
export default function Providers({ children }: { children: React.ReactNode }) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";
  return (
    <GoogleOAuthProvider clientId={clientId}>
      <AuthProvider>{children}</AuthProvider>
    </GoogleOAuthProvider>
  );
}
