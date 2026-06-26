import { useState } from 'react';
import { FileSpreadsheet } from 'lucide-react';
import { UploadStep } from '@/components/imports/UploadStep';
import { MapStep } from '@/components/imports/MapStep';
import { PreviewStep } from '@/components/imports/PreviewStep';
import { RunStep } from '@/components/imports/RunStep';
import type {
  WizardState,
  WizardStep,
  ImportEntity,
  MatchKey,
  ConflictPolicy,
  UploadColumn,
  PreviewCounts,
  RunCounts,
} from '@/types/imports';

const STEP_LABELS: Record<WizardStep, string> = {
  1: 'Upload',
  2: 'Map',
  3: 'Preview',
  4: 'Run',
};

function StepIndicator({ current }: { current: WizardStep }) {
  const steps: WizardStep[] = [1, 2, 3, 4];
  return (
    <nav aria-label="Import wizard steps" className="flex items-center gap-1">
      {steps.map((step, idx) => (
        <div key={step} className="flex items-center gap-1">
          <div
            className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold transition-colors ${
              step === current
                ? 'bg-primary text-primary-foreground'
                : step < current
                ? 'bg-primary/20 text-primary'
                : 'bg-muted text-foreground/40'
            }`}
            aria-current={step === current ? 'step' : undefined}
          >
            {step}
          </div>
          <span
            className={`hidden text-xs font-medium sm:inline ${
              step === current ? 'text-foreground' : 'text-foreground/40'
            }`}
          >
            {STEP_LABELS[step]}
          </span>
          {idx < steps.length - 1 && (
            <div
              className={`mx-1 hidden h-px w-6 sm:block ${
                step < current ? 'bg-primary/40' : 'bg-border'
              }`}
              aria-hidden="true"
            />
          )}
        </div>
      ))}
    </nav>
  );
}

const DEFAULT_STATE: WizardState = {
  step: 1,
  entity: 'contact',
  batchId: null,
  columns: [],
  sheets: [],
  selectedSheet: null,
  columnMap: {},
  matchKey: 'email',
  conflictPolicy: 'skip',
  targetEventId: null,
  previewCounts: null,
  runCounts: null,
  runElapsedMs: null,
  runBatchId: null,
};

export function ImportWizardPage() {
  const [state, setState] = useState<WizardState>(DEFAULT_STATE);

  const update = (patch: Partial<WizardState>) =>
    setState((prev) => ({ ...prev, ...patch }));

  const goTo = (step: WizardStep) => update({ step });

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Sticky header with step indicator */}
      <header className="sticky top-0 z-30 border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-3xl items-center gap-3">
          <FileSpreadsheet
            size={20}
            className="shrink-0 text-primary"
            aria-hidden="true"
          />
          <h1 className="text-base font-bold text-foreground">Import Wizard</h1>
          <div className="ml-auto">
            <StepIndicator current={state.step} />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 p-4 pb-24">
        {state.step === 1 && (
          <UploadStep
            entity={state.entity}
            onEntityChange={(entity: ImportEntity) => update({ entity, columnMap: {}, batchId: null, columns: [], sheets: [], selectedSheet: null })}
            onComplete={({
              batchId,
              columns,
              sheets,
              selectedSheet,
            }: {
              batchId: number;
              columns: UploadColumn[];
              sheets: string[];
              selectedSheet: string | null;
            }) => {
              update({ batchId, columns, sheets, selectedSheet, step: 2, columnMap: {} });
            }}
          />
        )}

        {state.step === 2 && state.batchId !== null && (
          <MapStep
            batchId={state.batchId}
            entity={state.entity}
            columns={state.columns}
            selectedSheet={state.selectedSheet}
            columnMap={state.columnMap}
            matchKey={state.matchKey}
            conflictPolicy={state.conflictPolicy}
            targetEventId={state.targetEventId}
            onColumnMapChange={(columnMap: Record<string, string>) => update({ columnMap })}
            onMatchKeyChange={(matchKey: MatchKey) => update({ matchKey })}
            onConflictPolicyChange={(conflictPolicy: ConflictPolicy) => update({ conflictPolicy })}
            onTargetEventIdChange={(targetEventId: number | null) => update({ targetEventId })}
            onBack={() => goTo(1)}
            onNext={() => update({ previewCounts: null, step: 3 })}
          />
        )}

        {state.step === 3 && state.batchId !== null && (
          <PreviewStep
            batchId={state.batchId}
            columnMap={state.columnMap}
            matchKey={state.matchKey}
            conflictPolicy={state.conflictPolicy}
            selectedSheet={state.selectedSheet}
            targetEventId={state.targetEventId}
            previewCounts={state.previewCounts}
            onPreviewCounts={(previewCounts: PreviewCounts) => update({ previewCounts })}
            onConflictPolicyChange={(conflictPolicy: ConflictPolicy) =>
              update({ conflictPolicy, previewCounts: null })
            }
            onBack={() => goTo(2)}
            onNext={() => goTo(4)}
          />
        )}

        {state.step === 4 && state.batchId !== null && (
          <RunStep
            batchId={state.batchId}
            columnMap={state.columnMap}
            matchKey={state.matchKey}
            conflictPolicy={state.conflictPolicy}
            selectedSheet={state.selectedSheet}
            targetEventId={state.targetEventId}
            runCounts={state.runCounts}
            runElapsedMs={state.runElapsedMs}
            runBatchId={state.runBatchId}
            onRunComplete={({
              batchId,
              counts,
              elapsedMs,
            }: {
              batchId: number;
              counts: RunCounts;
              elapsedMs: number;
            }) =>
              update({ runBatchId: batchId, runCounts: counts, runElapsedMs: elapsedMs })
            }
            onBack={() => goTo(3)}
          />
        )}
      </main>
    </div>
  );
}
