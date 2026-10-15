import CreateForm from "@/components/CreateForm";
import RecentJobs from "@/components/RecentJobs";

export default function Home() {
  return (
    <>
      <section className="pt-10 sm:pt-14">
        <h1 className="text-2xl font-semibold tracking-tight">New video</h1>
        <p className="mt-1 mb-6 text-sm text-muted">
          Give it a topic. It writes a script, finds footage, records a voiceover and renders an mp4.
        </p>
        <CreateForm />
      </section>
      <RecentJobs />
    </>
  );
}
