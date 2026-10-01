import { useState } from "react";
import { MoreVertical, Archive, ArchiveRestore, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { archivePersonDocument, deletePersonDocumentForever, unarchivePersonDocument } from "@/lib/api";

/** Shared "..." menu for a PersonDocument — Archive/Unarchive (soft,
 * reversible, the recommended action) and Delete Forever (a deliberate,
 * permanent exception to the project's usual no-hard-deletes convention,
 * only for cleaning up known-bad early test audits). Used from both the
 * Audits list row and the audit detail page itself. */
export function AuditActionsMenu({
  personDocumentId,
  active,
  onChanged,
  onDeleted,
}: {
  personDocumentId: string;
  active: boolean;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [deleting, setDeleting] = useState(false);

  async function handleArchiveToggle() {
    if (active) {
      await archivePersonDocument(personDocumentId, "k.kumar@masterfaster.org");
    } else {
      await unarchivePersonDocument(personDocumentId, "k.kumar@masterfaster.org");
    }
    onChanged();
  }

  async function handleDeleteForever() {
    setDeleting(true);
    try {
      await deletePersonDocumentForever(personDocumentId, "k.kumar@masterfaster.org", reason.trim() || "no reason given");
      onDeleted();
    } finally {
      setDeleting(false);
      setConfirmOpen(false);
      setReason("");
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="More actions">
            <MoreVertical className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={handleArchiveToggle}>
            {active ? (
              <>
                <Archive className="mr-2 h-4 w-4" /> Archive
              </>
            ) : (
              <>
                <ArchiveRestore className="mr-2 h-4 w-4" /> Unarchive
              </>
            )}
          </DropdownMenuItem>
          <DropdownMenuItem
            className="text-destructive focus:text-destructive"
            onSelect={(e) => {
              e.preventDefault();
              setConfirmOpen(true);
            }}
          >
            <Trash2 className="mr-2 h-4 w-4" /> Delete Forever
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this audit forever?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes this document, its review, and all of its findings. This{" "}
              <strong>cannot be undone</strong> — there is no way to recover it afterward. If you just want it out of
              the way, use Archive instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="delete-reason">Reason (optional, kept in the audit trail)</Label>
            <Input
              id="delete-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. known-bad pre-fix test extraction"
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleting}
              onClick={(e) => {
                e.preventDefault();
                handleDeleteForever();
              }}
            >
              {deleting ? "Deleting…" : "Delete Forever"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
