import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider, RequireAuth } from "@/lib/auth";
import { Toaster } from "@/components/ui/toaster";
import { SidebarProvider } from "@/components/ui/sidebar";
import { ThemeProvider } from "@/components/theme-provider";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Spinr Admin",
  description: "Admin Dashboard for the Spinr Rideshare Platform",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans antialiased`}>
        <AuthProvider>
          <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
            <SidebarProvider>
              <RequireAuth>
                <TooltipProvider>
                  {children}
                  <Toaster />
                </TooltipProvider>
              </RequireAuth>
            </SidebarProvider>
          </ThemeProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
