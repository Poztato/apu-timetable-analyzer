interface BrandIconProps {
  className: string;
}

export function BrandIcon({ className }: BrandIconProps) {
  return (
    <img
      className={className}
      src={`${import.meta.env.BASE_URL}favicon.webp`}
      alt=""
      aria-hidden="true"
    />
  );
}
