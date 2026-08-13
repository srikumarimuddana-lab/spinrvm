"use client";

import { useRef, useEffect } from "react";
import { Bold, Italic, Underline, List, ListOrdered, Link as LinkIcon } from "lucide-react";

/**
 * Minimal dependency-free rich-text editor (contentEditable + a small toolbar)
 * that emits HTML. Used for ticket replies so agents can send formatted email
 * (bold, lists, links, line breaks) instead of a single collapsed line.
 *
 * Uncontrolled while the user types (keeps the caret stable); external value
 * changes are synced in only when the editor is NOT focused — covers parent
 * resets (value="" after sending) and programmatic inserts (e.g. an AI-drafted
 * reply). Writing innerHTML mid-typing would reset the caret, so we skip it
 * while focused (during typing value already mirrors innerHTML via onInput).
 */

// Hoisted out of RichTextEditor — a component defined inside another
// component's render body is recreated (a new identity) on every render,
// which the React Compiler flags (react-hooks/static-components) since it
// defeats memoization and can cause the toolbar buttons to remount instead
// of just re-rendering. Takes its command/handler as props instead of
// closing over the parent's render-scope variables.
function ToolbarButton({
    title,
    disabled,
    onClick,
    children,
}: {
    title: string;
    disabled?: boolean;
    onClick: () => void;
    children: React.ReactNode;
}) {
    return (
        <button
            type="button"
            title={title}
            disabled={disabled}
            onMouseDown={(e) => e.preventDefault()}
            onClick={onClick}
            className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
        >
            {children}
        </button>
    );
}

export function RichTextEditor({
    value,
    onChange,
    placeholder,
    minHeight = 140,
    disabled,
}: {
    value: string;
    onChange: (html: string) => void;
    placeholder?: string;
    minHeight?: number;
    disabled?: boolean;
}) {
    const ref = useRef<HTMLDivElement>(null);

    // Sync an external value into the uncontrolled editor when it diverges and
    // the user isn't actively typing — covers parent clears (value="") and
    // programmatic inserts (AI draft). Skipped while focused to protect the caret.
    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        if (document.activeElement !== el && el.innerHTML !== (value || "")) {
            el.innerHTML = value || "";
        }
    }, [value]);

    const emit = () => onChange(ref.current?.innerHTML || "");

    const exec = (cmd: string, arg?: string) => {
        ref.current?.focus();
        // execCommand is deprecated but remains the simplest cross-browser way
        // to do inline formatting in a contentEditable without a heavy editor.
        document.execCommand(cmd, false, arg);
        emit();
    };

    const addLink = () => {
        const url = window.prompt("Link URL (https://…)");
        if (url) exec("createLink", url);
    };

    return (
        <div className="rounded-md border focus-within:ring-1 focus-within:ring-ring">
            <div className="flex items-center gap-0.5 border-b px-1 py-1">
                <ToolbarButton title="Bold" disabled={disabled} onClick={() => exec("bold")}>
                    <Bold className="h-4 w-4" />
                </ToolbarButton>
                <ToolbarButton title="Italic" disabled={disabled} onClick={() => exec("italic")}>
                    <Italic className="h-4 w-4" />
                </ToolbarButton>
                <ToolbarButton title="Underline" disabled={disabled} onClick={() => exec("underline")}>
                    <Underline className="h-4 w-4" />
                </ToolbarButton>
                <span className="mx-1 h-4 w-px bg-border" />
                <ToolbarButton title="Bullet list" disabled={disabled} onClick={() => exec("insertUnorderedList")}>
                    <List className="h-4 w-4" />
                </ToolbarButton>
                <ToolbarButton title="Numbered list" disabled={disabled} onClick={() => exec("insertOrderedList")}>
                    <ListOrdered className="h-4 w-4" />
                </ToolbarButton>
                <span className="mx-1 h-4 w-px bg-border" />
                <ToolbarButton title="Insert link" disabled={disabled} onClick={addLink}>
                    <LinkIcon className="h-4 w-4" />
                </ToolbarButton>
            </div>
            <div
                ref={ref}
                contentEditable={!disabled}
                onInput={emit}
                data-placeholder={placeholder}
                style={{ minHeight }}
                suppressContentEditableWarning
                className="prose-sm max-w-none overflow-auto p-3 text-sm outline-none [&_a]:text-blue-600 [&_a]:underline [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5 empty:before:text-muted-foreground empty:before:content-[attr(data-placeholder)]"
            />
        </div>
    );
}
