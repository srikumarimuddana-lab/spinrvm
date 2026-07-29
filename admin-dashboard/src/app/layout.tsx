import type { Metadata } from "next";
import { headers } from "next/headers";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

// Loaded unconditionally (cheap — a variable font, no visual effect until a
// `.theme-v2` ancestor is present) so the epic #2785 Phase 3 restyle can be
// toggled purely in CSS via the `admin_theme_v2_enabled` flag, with no
// server-side re-render needed when a client toggles it.
const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-plus-jakarta-sans",
});
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { SidebarProvider } from "@/components/ui/sidebar";
import { ThemeProvider } from "@/components/theme-provider";
import { AnalyticsWrapper } from "@/components/analytics-wrapper";
import { AuthInitializer } from "@/components/auth-initializer";

export const metadata: Metadata = {
  title: "Spinr Admin",
  description: "Admin Dashboard for the Spinr Rideshare Platform",
  robots: { index: false, follow: false },
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const headersList = await headers();
  const nonce = headersList.get("x-nonce") ?? undefined;

  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`font-sans antialiased ${plusJakartaSans.variable}`}>
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
        <AnalyticsWrapper />
      </body>
    </html>
  );
}