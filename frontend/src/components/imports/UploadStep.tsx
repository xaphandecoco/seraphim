import { useRef, useState, useCallback } from 'react';
import { Upload, FileText } from 'lucide-react';
import { toast } from 'sonner';
import { useMutation } from '@tanstack/react-query';
import { uploadImport, getColumns } from '@/services/imports';
import { LoadingState } from '@/components/ui/StateViews';
import type { UploadColumn, ImportEntity } from '@/types/imports';

interface UploadStepProps {
  entity: ImportEntity;
  onEntityChange: (entity: ImportEntity) => void;
  onComplete: (args: {
    batchId: number;
    columns: UploadColumn[];
    sheets: string[];
    selectedSheet: string | null;
  }) => void;
}

const ACCEPT = '.csv,.xlsx';
const MAX_MB = 15;

function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UploadStep({ entity, onEntityChange, onComplete }: UploadStepProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedSheet, setSelectedSheet] = useState<string | null>(null);
  const [availableSheets, setAvailableSheets] = useState<string[]>([]);
  const [uploadedBatchId, setUploadedBatchId] = useState<number | null>(null);
  const [uploadedColumns, setUploadedColumns] = useState<UploadColumn[]>([]);

  const uploadMutation = useMutation({
    mutationFn: ({ file, ent }: { file: File; ent: ImportEntity }) =>
      uploadImport(file, ent),
    onSuccess: (data) => {
      setUploadedBatchId(data.batch_id);
      setUploadedColumns(data.columns);
      setAvailableSheets(data.sheets);
      if (data.sheets.length > 1) {
        setSelectedSheet(data.sheets[0]);
      } else {
        setSelectedSheet(null);
      }
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'File upload failed. Check the file format and size.');
    },
  });

  const sheetColumnsMutation = useMutation({
    mutationFn: ({ batchId, sheet }: { batchId: number; sheet: string }) =>
      getColumns(batchId, sheet),
    onSuccess: (data) => {
      const cols: UploadColumn[] = data.headers.map((h) => ({ name: h, sample: [] }));
      setUploadedColumns(cols);
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Failed to load sheet columns.');
    },
  });

  const handleFile = useCallback(
    (file: File) => {
      if (!file.name.match(/\.(csv|xlsx)$/i)) {
        toast.error('Only .csv and .xlsx files are supported.');
        return;
      }
      if (file.size > MAX_MB * 1024 * 1024) {
        toast.error(`File exceeds the ${MAX_MB} MB limit (${formatBytes(file.size)}).`);
        return;
      }
      setSelectedFile(file);
      setUploadedBatchId(null);
      setUploadedColumns([]);
      setAvailableSheets([]);
      setSelectedSheet(null);
      uploadMutation.mutate({ file, ent: entity });
    },
    [entity, uploadMutation],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleSheetChange = (sheet: string) => {
    setSelectedSheet(sheet);
    if (uploadedBatchId) {
      sheetColumnsMutation.mutate({ batchId: uploadedBatchId, sheet });
    }
  };

  const handleNext = () => {
    if (!uploadedBatchId) return;
    onComplete({
      batchId: uploadedBatchId,
      columns: uploadedColumns,
      sheets: availableSheets,
      selectedSheet,
    });
  };

  const isUploading = uploadMutation.isPending || sheetColumnsMutation.isPending;
  const canNext = !!uploadedBatchId && !isUploading;

  return (
    <div className="space-y-6">
      {/* Entity selector */}
      <fieldset>
        <legend className="mb-2 text-sm font-semibold text-foreground">What are you importing?</legend>
        <div className="flex gap-3">
          {(['contact', 'participant'] as ImportEntity[]).map((ent) => (
            <label
              key={ent}
              className={`flex cursor-pointer items-center gap-2 rounded-xl border px-4 py-3 text-sm font-semibold transition-colors ${
                entity === ent
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border bg-card text-foreground hover:bg-primary/5'
              }`}
            >
              <input
                type="radio"
                name="entity"
                value={ent}
                checked={entity === ent}
                onChange={() => {
                  onEntityChange(ent);
                  setUploadedBatchId(null);
                  setUploadedColumns([]);
                  setAvailableSheets([]);
                  setSelectedSheet(null);
                  setSelectedFile(null);
                }}
                className="sr-only"
              />
              {ent === 'contact' ? 'Contacts' : 'Participants'}
            </label>
          ))}
        </div>
      </fieldset>

      {/* Drop zone */}
      <div
        role="button"
        tabIndex={0}
        aria-label="Drop a CSV or Excel file here, or click to browse"
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click(); }}
        className={`flex min-h-[160px] cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed p-8 transition-colors ${
          dragging
            ? 'border-primary bg-primary/10'
            : 'border-border bg-card hover:border-primary/50 hover:bg-primary/5'
        }`}
      >
        <Upload
          size={32}
          className={dragging ? 'text-primary' : 'text-foreground/30'}
          aria-hidden="true"
        />
        <div className="text-center">
          <p className="text-sm font-semibold text-foreground">
            Drop a file here, or click to browse
          </p>
          <p className="mt-1 text-xs text-foreground/50">
            .csv or .xlsx · max {MAX_MB} MB · max 50,000 rows
          </p>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
            e.target.value = '';
          }}
        />
      </div>

      {/* Selected file info */}
      {selectedFile && (
        <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3">
          <FileText size={18} className="shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-foreground">{selectedFile.name}</p>
            <p className="text-xs text-foreground/50">{formatBytes(selectedFile.size)}</p>
          </div>
          {isUploading && (
            <span className="text-xs text-foreground/50">Uploading…</span>
          )}
        </div>
      )}

      {/* Upload loading */}
      {isUploading && <LoadingState message="Parsing file…" />}

      {/* Sheet picker (for multi-sheet XLSX) */}
      {availableSheets.length > 1 && !isUploading && (
        <div>
          <label className="mb-1 block text-sm font-semibold text-foreground" htmlFor="sheet-select">
            Select worksheet
          </label>
          <select
            id="sheet-select"
            value={selectedSheet ?? ''}
            onChange={(e) => handleSheetChange(e.target.value)}
            className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {availableSheets.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      )}

      {/* Upload result summary */}
      {uploadedBatchId && !isUploading && (
        <div className="rounded-xl border border-border bg-card px-4 py-3">
          <p className="text-sm text-foreground/70">
            Detected <span className="font-semibold text-foreground">{uploadedColumns.length}</span> columns.
            Ready to map.
          </p>
        </div>
      )}

      {/* Footer */}
      <div className="flex justify-end pt-2">
        <button
          type="button"
          onClick={handleNext}
          disabled={!canNext}
          className="rounded-xl bg-primary px-6 py-2.5 text-sm font-bold text-primary-foreground shadow-sm transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next: Map columns
        </button>
      </div>
    </div>
  );
}
