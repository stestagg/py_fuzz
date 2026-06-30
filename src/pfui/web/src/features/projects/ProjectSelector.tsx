import { Button, MenuItem } from "@blueprintjs/core";
import { Select } from "@blueprintjs/select";

const ProjectSelect = Select.ofType<string>();

export function ProjectSelector({ projects, selected, onSelect, onCreate }: {
  projects: string[];
  selected: string | null;
  onSelect: (project: string) => void;
  onCreate: (initialName?: string) => void;
}) {
  const select = (project: string) => projects.includes(project) ? onSelect(project) : onCreate(project);
  return (
    <div className="project-picker">
      <ProjectSelect
        items={projects}
        itemPredicate={(query, item) => item.toLowerCase().includes(query.toLowerCase())}
        itemRenderer={(item, { handleClick, modifiers }) => (
          <MenuItem
            active={modifiers.active}
            disabled={modifiers.disabled}
            key={item}
            onClick={handleClick}
            roleStructure="listoption"
            selected={item === selected}
            text={item}
          />
        )}
        createNewItemFromQuery={(query) => query.trim()}
        createNewItemRenderer={(query, active, handleClick) => {
          const name = query.trim();
          if (!name || projects.some((project) => project.toLowerCase() === name.toLowerCase())) return undefined;
          return <MenuItem active={active} icon="add" key="create-project" onClick={handleClick} roleStructure="listoption" text={`Create project “${name}”`} />;
        }}
        noResults={<MenuItem disabled text="No matching projects" roleStructure="listoption" />}
        onItemSelect={select}
        popoverProps={{ minimal: true, matchTargetWidth: true }}
        filterable
      >
        <Button
          alignText="left"
          icon="folder-open"
          rightIcon="caret-down"
          text={selected ?? "Select project"}
        />
      </ProjectSelect>
      <Button minimal icon="add" text="Create" onClick={() => onCreate()} />
    </div>
  );
}
