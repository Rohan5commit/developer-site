import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'AidAtlas AI',
  description: 'AI-powered aid and disaster response platform',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
