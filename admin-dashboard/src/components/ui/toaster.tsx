"use client"

import {
    Toast,
    ToastClose,
    ToastProvider,
    ToastTitle,
    ToastDescription,
    ToastViewport,
} from "@/components/ui/toast"
import { useToast } from "@/components/ui/use-toast"

function Toaster() {
    const { toasts } = useToast()

    return (
        <ToastProvider>
            {toasts.map(function ({ id, title, description, action, ...props }) {
                // Errors stay until dismissed and are announced assertively; other
                // toasts auto-dismiss and are announced politely. A caller's own
                // duration or type still wins.
                const isError = props.variant === "destructive"
                return (
                    <Toast
                        key={id}
                        duration={isError ? Infinity : undefined}
                        type={isError ? "foreground" : "background"}
                        {...props}
                    >
                        <div className="grid gap-1">
                            {title && <ToastTitle>{title}</ToastTitle>}
                            {description && (
                                <ToastDescription>{description}</ToastDescription>
                            )}
                        </div>
                        {action}
                        <ToastClose />
                    </Toast>
                )
            })}
            <ToastViewport />
        </ToastProvider>
    )
}

export { Toaster }
