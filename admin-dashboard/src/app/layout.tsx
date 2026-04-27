import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { SidebarProvider } from "@/components/ui/sidebar";
import { ThemeProvider } from "@/components/theme-provider";
import { Analytics } from "@vercel/analytics/next";
import { AuthInitializer } from "@/components/auth-initializer";

export const metadata: Metadata = {
  title: "Spinr Admin",
  description: "Admin Dashboard for the Spinr Rideshare Platform",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const headersList = await headers();
  const nonce = headersList.get("x-nonce") ?? undefined;

  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem
          disableTransitionOnChange
          nonce={nonce}
        >
          <SidebarProvider>
            <TooltipProvider>
              <AuthInitializer />
              {children}
              <Toaster />
            </TooltipProvider>
          </SidebarProvider>
        </ThemeProvider>
        {/* Strip dynamic entity IDs from page paths before they reach Vercel's US
            edge ingestion — /dashboard/drivers/abc123 → /dashboard/drivers/[id] ([22-3]) */}
        <Analytics
          beforeSend={(event) => ({
            ...event,
            url: event.url.replace(/\/[0-9a-f-]{8,}(?=\/|$)/gi, '/[id]'),
          })}
        />
      </body>
    </html>
  );
}