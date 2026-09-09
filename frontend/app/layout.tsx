import "./globals.css";

export const metadata = {
  title: "Pungol",
  description: "Turn long YouTube videos into short, ready-to-post clips.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
