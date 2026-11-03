import { WelcomeScreen } from '@/components/welcome-screen';

// App entry: the brand intro, which settles into the welcome screen in place
// (see components/welcome-screen.tsx for the motion spec).
export default function Index() {
  return <WelcomeScreen intro />;
}
