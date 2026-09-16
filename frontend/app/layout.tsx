import "./globals.css";

import Providers from "./providers";

export const metadata = {
  title: "Mananabas",
  description: "Turn long YouTube videos into short, ready-to-post clips.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
