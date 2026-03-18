"use client"

import {
    Toast,
    ToastClose,
    ToastDescription,
    ToastProvider,
    ToastTitle,
    ToastViewport,
} from "@/components/ui/toast"

export {
    Toast,
    ToastClose,
    ToastDescription,
    ToastProvider,
    ToastTitle,
    ToastViewport,
}

export function Toaster() {
    return (
        <ToastProvider>
            <Toast>
                <ToastTitle>Scheduled: Catch up</ToastTitle>
                <ToastDescription>
                    See you on Friday morning at 8am!
                </ToastDescription>
                <ToastClose />
            </Toast>
            <ToastViewport />
        </ToastProvider>
    )
}
