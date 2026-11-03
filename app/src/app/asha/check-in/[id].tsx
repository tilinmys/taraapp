import { Redirect, useLocalSearchParams } from 'expo-router';

// Old entry to a check-in. The separate "Before you start" page repeated
// what Sakhi's own intro says, so a check-in now opens Sakhi directly
// (asha/session/[id]); this keeps old links working.
export default function CheckInRedirect() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <Redirect href={{ pathname: '/asha/session/[id]', params: { id } }} />;
}
