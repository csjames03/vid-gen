import { Suspense } from "react";
import JobView from "@/components/JobView";

// The job id is only known at request time, so (with Cache Components) param access
// sits inside a Suspense boundary; JobView unwraps the params promise with use().
export default function JobPage({ params }: PageProps<"/jobs/[id]">) {
  return (
    <Suspense fallback={<div className="mt-10 h-80 animate-pulse rounded-lg bg-subtle" />}>
      <JobView params={params} />
    </Suspense>
  );
}
