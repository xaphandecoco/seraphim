import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FilterBuilder } from './FilterBuilder';
import type { CriteriaGroup, FieldSpec } from '@/types/search';

// ContactPickerModal uses api internally — stub it so it doesn't throw in tests
vi.mock('@/services/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

const mockFields: FieldSpec[] = [
  {
    key: 'display_name',
    label: 'Display Name',
    kind: 'core',
    type: 'string',
    ops: ['eq', 'contains', 'is_set', 'is_empty'],
    nullable: false,
  },
  {
    key: 'tier',
    label: 'Tier',
    kind: 'derived',
    type: 'enum',
    ops: ['eq', 'is_set'],
    options: [{ value: 'tier0', label: 'Tier 0' }],
    nullable: true,
  },
];

const emptyGroup: CriteriaGroup = { logic: 'and', conditions: [] };

describe('FilterBuilder', () => {
  it('renders AND logic toggle button', () => {
    render(<FilterBuilder value={emptyGroup} fields={mockFields} onChange={() => {}} />);
    expect(
      screen.getByRole('button', { name: /logic: and/i }),
    ).toBeInTheDocument();
  });

  it('toggles logic from AND to OR when logic button is clicked', () => {
    let captured: CriteriaGroup | undefined;
    render(
      <FilterBuilder
        value={emptyGroup}
        fields={mockFields}
        onChange={(g) => {
          captured = g;
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /logic: and/i }));
    expect(captured?.logic).toBe('or');
  });

  it('adds a leaf condition when "Add condition" is clicked', () => {
    const onChange = vi.fn();
    render(
      <FilterBuilder value={emptyGroup} fields={mockFields} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /add condition/i }));
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange.mock.calls[0][0].conditions).toHaveLength(1);
  });

  it('adds a nested group when "Add group" is clicked', () => {
    const onChange = vi.fn();
    render(
      <FilterBuilder value={emptyGroup} fields={mockFields} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /add group/i }));
    expect(onChange).toHaveBeenCalledOnce();
    const firstCondition = onChange.mock.calls[0][0].conditions[0] as CriteriaGroup;
    expect('logic' in firstCondition).toBe(true);
    expect(firstCondition.logic).toBe('and');
  });

  it('shows empty-conditions message when group has no conditions', () => {
    render(
      <FilterBuilder value={emptyGroup} fields={mockFields} onChange={() => {}} />,
    );
    expect(screen.getByText(/no conditions yet/i)).toBeInTheDocument();
  });

  it('renders "Remove group" button only when onRemove prop is provided', () => {
    const { rerender } = render(
      <FilterBuilder value={emptyGroup} fields={mockFields} onChange={() => {}} />,
    );
    expect(
      screen.queryByRole('button', { name: /remove group/i }),
    ).toBeNull();

    rerender(
      <FilterBuilder
        value={emptyGroup}
        fields={mockFields}
        onChange={() => {}}
        onRemove={() => {}}
      />,
    );
    expect(
      screen.getByRole('button', { name: /remove group/i }),
    ).toBeInTheDocument();
  });

  it('calls onRemove when the remove group button is clicked', () => {
    const onRemove = vi.fn();
    render(
      <FilterBuilder
        value={emptyGroup}
        fields={mockFields}
        onChange={() => {}}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /remove group/i }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it('renders leaf row with field picker when a condition exists', () => {
    const groupWithLeaf: CriteriaGroup = {
      logic: 'and',
      conditions: [{ field: 'display_name', op: 'contains', value: 'Alice' }],
    };
    render(
      <FilterBuilder value={groupWithLeaf} fields={mockFields} onChange={() => {}} />,
    );
    expect(screen.getByRole('combobox', { name: /select field/i })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /remove condition/i }),
    ).toBeInTheDocument();
  });

  it('hides value input when op is is_set', () => {
    const groupWithIsSet: CriteriaGroup = {
      logic: 'and',
      conditions: [{ field: 'display_name', op: 'is_set' }],
    };
    render(
      <FilterBuilder value={groupWithIsSet} fields={mockFields} onChange={() => {}} />,
    );
    // No value input should be rendered for is_set
    expect(screen.queryByRole('textbox', { name: /value/i })).toBeNull();
  });

  it('renders nested FilterBuilder for a sub-group condition', () => {
    const groupWithSubGroup: CriteriaGroup = {
      logic: 'and',
      conditions: [{ logic: 'or', conditions: [] }],
    };
    render(
      <FilterBuilder value={groupWithSubGroup} fields={mockFields} onChange={() => {}} />,
    );
    // Two logic buttons: root AND + nested OR
    const logicButtons = screen.getAllByRole('button', { name: /logic:/i });
    expect(logicButtons).toHaveLength(2);
  });
});
