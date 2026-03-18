"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/hooks/use-mobile";

const SidebarContext = React.createContext<{
  state: "expanded" | "collapsed";
  toggle: () => void;
}>({
  state: "expanded",
  toggle: () => {},
});

const SidebarProvider = ({ children }: { children: React.ReactNode }) => {
  const [state, setState] = React.useState<"expanded" | "collapsed">("expanded");
  const isMobile = useIsMobile();

  const toggle = () => {
    if (isMobile) {
      setState(prev => prev === "expanded" ? "collapsed" : "expanded");
    } else {
      setState(prev => prev === "expanded" ? "collapsed" : "expanded");
    }
  };

  return (
    <SidebarContext.Provider value={{ state, toggle }}>
      {children}
    </SidebarContext.Provider>
  );
};

const useSidebar = () => React.useContext(SidebarContext);

const Sidebar = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  return (
    <div
      className={cn(
        "flex h-full w-[240px] flex-col gap-6 overflow-hidden border-r bg-background p-4",
        className
      )}
      {...props}
    />
  );
};

const SidebarHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  return (
    <div
      className={cn("flex flex-col gap-2 text-sm", className)}
      {...props}
    />
  );
};

const SidebarFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  return (
    <div
      className={cn("mt-auto flex flex-col gap-2", className)}
      {...props}
    />
  );
};

const SidebarContent = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-1 overflow-auto",
        className
      )}
      {...props}
    />
  );
};

const SidebarGroup = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  return (
    <div
      className={cn("flex flex-col gap-1", className)}
      {...props}
    />
  );
};

const SidebarItem = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  return (
    <div
      className={cn("flex flex-col gap-1", className)}
      {...props}
    />
  );
};

const SidebarTrigger = React.forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<"button">
>(({ className, ...props }, ref) => {
  const { toggle } = useSidebar();

  return (
    <button
      ref={ref}
      onClick={toggle}
      className={cn("rounded-md p-2 hover:bg-accent", className)}
      {...props}
    />
  );
});
SidebarTrigger.displayName = "SidebarTrigger";

export {
  SidebarProvider,
  useSidebar,
  Sidebar,
  SidebarHeader,
  SidebarFooter,
  SidebarContent,
  SidebarGroup,
  SidebarItem,
  SidebarTrigger,
};