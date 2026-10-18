import { FluffyMascot } from "@/components/FluffyMascot";

export default function FluffyBrand({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tighter ${className}`}>
      <FluffyMascot activity="static" size={28} />
      Fluffy
    </span>
  );
}
