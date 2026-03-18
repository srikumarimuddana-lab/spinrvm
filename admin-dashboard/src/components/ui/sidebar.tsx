"use client"

import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { PanelLeft } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

export interface SidebarContextValue {
    isOpen: boolean
    setIsOpen: (open: boolean) => void
}

const SidebarContext = React.createContext<SidebarContextValue | undefined>(undefined)

function useSidebar() {
    const context = React.useContext(SidebarContext)
    if (!context) {
        throw new Error("useSidebar must be used within a SidebarProvider.")
    }
    return context
}

interface SidebarProviderProps extends React.ComponentProps<"div"> {
    defaultOpen?: boolean
    open?: boolean
    onOpenChange?: (open: boolean) => void
}

const SidebarProvider = React.forwardRef<
    HTMLDivElement,
    SidebarProviderProps
>(({
    defaultOpen = true,
    open: openProp,
    onOpenChange: setOpenProp,
    className,
    children,
    ...props
}, ref) => {
    const [open, setOpen] = React.useState(defaultOpen)

    const openState = openProp !== undefined ? openProp : open
    const setOpenState = setOpenProp !== undefined ? setOpenProp : setOpen

    return (
        <SidebarContext.Provider value={{ isOpen: openState, setIsOpen: setOpenState }}>
            <div
                ref={ref}
                className={cn(
                    "flex min-h-screen w-full",
                    className
                )}
                {...props}
            >
                {children}
            </div>
        </SidebarContext.Provider>
    )
})
SidebarProvider.displayName = "SidebarProvider"

interface SidebarProps extends React.ComponentProps<"aside"> {
    side?: "left" | "right"
    variant?: "sidebar" | "floating" | "inset"
}

const Sidebar = React.forwardRef<HTMLDivElement, SidebarProps>(
    ({ className, side = "left", variant = "sidebar", ...props }, ref) => {
        const { isOpen, setIsOpen } = useSidebar()

        return (
            <aside
                ref={ref}
                className={cn(
                    "fixed top-0 z-40 h-screen w-64 border-r bg-background transition-transform",
                    side === "left" ? "left-0" : "right-0",
                    !isOpen && (side === "left" ? "-translate-x-full" : "translate-x-full"),
                    className
                )}
                {...props}
            >
                <div className="flex h-16 items-center border-b px-4">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setIsOpen(!isOpen)}
                    >
                        <PanelLeft className="h-4 w-4" />
                    </Button>
                </div>
                <div className="p-4">
                    {props.children}
                </div>
            </aside>
        )
    }
)
Sidebar.displayName = "Sidebar"

const SidebarTrigger = React.forwardRef<
    React.ElementRef<typeof Button>,
    React.ComponentPropsWithoutRef<typeof Button>
>(({ className, ...props }, ref) => {
    const { isOpen, setIsOpen } = useSidebar()

    return (
        <Button
            ref={ref}
            variant="ghost"
            size="icon"
            onClick={() => setIsOpen(!isOpen)}
            className={className}
            {...props}
        />
    )
})
SidebarTrigger.displayName = "SidebarTrigger"

export {
    Sidebar,
    SidebarProvider,
    SidebarTrigger,
    useSidebar,
}
