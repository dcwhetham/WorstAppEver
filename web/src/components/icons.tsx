/**
 * Inline SVG icons.
 *
 * Hand-rolled instead of an icon package: this is roughly a dozen glyphs, and
 * they inherit `currentColor` and stroke width from context, which a sprite sheet
 * or font would not. It also keeps the client bundle free of a dependency whose
 * tree-shaking has to be verified.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export const StarIcon = ({ filled = false, ...props }: IconProps & { filled?: boolean }) => (
  <Icon {...props} fill={filled ? "currentColor" : "none"}>
    <path d="M12 3.5l2.6 5.3 5.9.85-4.25 4.15 1 5.85L12 16.9l-5.25 2.75 1-5.85L3.5 9.65l5.9-.85L12 3.5z" />
  </Icon>
);

export const SearchIcon = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="M20 20l-4.2-4.2" />
  </Icon>
);

export const PhotoIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
    <circle cx="8.75" cy="10" r="1.6" />
    <path d="M3.5 17l4.8-4.3a2 2 0 012.7 0L20 20" />
  </Icon>
);

export const VideoIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="2.5" y="6" width="13.5" height="12" rx="2.5" />
    <path d="M16 11l5.5-3v8L16 13z" />
  </Icon>
);

export const ClockIcon = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Icon>
);

export const PlayIcon = (props: IconProps) => (
  <Icon {...props} fill="currentColor" stroke="none">
    <path d="M8 5.5l11 6.5-11 6.5z" />
  </Icon>
);

export const RefreshIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M20 12a8 8 0 11-2.6-5.9" />
    <path d="M20 4v4h-4" />
  </Icon>
);

export const DownloadIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 3.5v11" />
    <path d="M7.5 10.5L12 15l4.5-4.5" />
    <path d="M4.5 18.5h15" />
  </Icon>
);

export const AlertIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 4.5l8.5 15h-17l8.5-15z" />
    <path d="M12 10v4" />
    <path d="M12 17h.01" />
  </Icon>
);

export const CloseIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Icon>
);

export const ChevronLeftIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M14.5 5.5L8 12l6.5 6.5" />
  </Icon>
);

export const ChevronRightIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M9.5 5.5L16 12l-6.5 6.5" />
  </Icon>
);

export const ChevronDownIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M5.5 9.5L12 16l6.5-6.5" />
  </Icon>
);

export const LinkIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M10 13.5a3.5 3.5 0 005 0l3-3a3.54 3.54 0 00-5-5l-1 1" />
    <path d="M14 10.5a3.5 3.5 0 00-5 0l-3 3a3.54 3.54 0 005 5l1-1" />
  </Icon>
);

export const PlusIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 5.5v13M5.5 12h13" />
  </Icon>
);

export const TrashIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M4.5 7h15" />
    <path d="M9.5 7V5h5v2" />
    <path d="M6.5 7l.8 12h9.4l.8-12" />
  </Icon>
);

export const LayersIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 3.5l8.5 4.5L12 12.5 3.5 8z" />
    <path d="M4 12.5l8 4.2 8-4.2" />
    <path d="M4 16.8l8 4.2 8-4.2" />
  </Icon>
);

export const TerminalIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
    <path d="M7.5 10l2.5 2-2.5 2" />
    <path d="M12.5 14h4" />
  </Icon>
);

export const PowerIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 3.5v7" />
    <path d="M7.5 6.8a6.5 6.5 0 109 0" />
  </Icon>
);

export const ArchiveIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="4.5" width="18" height="4.5" rx="1.5" />
    <path d="M4.5 9v9a1.5 1.5 0 001.5 1.5h12a1.5 1.5 0 001.5-1.5V9" />
    <path d="M9.5 13h5" />
  </Icon>
);

export const CheckIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Icon>
);
