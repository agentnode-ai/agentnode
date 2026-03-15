interface FeatureCardProps {
  icon: string;
  title: string;
  description: string;
}

export default function FeatureCard({ icon, title, description }: FeatureCardProps) {
  return (
    <div className="group rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30 hover:bg-card/80">
      <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-lg">
        {icon}
      </div>
      <h3 className="mb-2 text-lg font-semibold text-foreground">{title}</h3>
      <p className="text-sm leading-relaxed text-muted">{description}</p>
    </div>
  );
}
