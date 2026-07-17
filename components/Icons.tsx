import React from 'react';
import {
  ShieldCheck,
  AlertTriangle,
  AlertCircle,
  Wrench,
  Gauge,
  CheckCircle2,
  ChevronDown,
  Loader2,
  ArrowRight,
  ArrowLeft,
  BrainCircuit,
  Database,
  Route,
  X,
  MapPin,
  Mail,
  Car,
  Check,
  Heart,
  Phone,
  User,
  Clock,
  Share2,
  Link2,
  MessageCircle,
  BellRing,
  CalendarDays,
  CarFront,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Lightbulb
} from 'lucide-react';

// Purpose-built tyre pictogram: outer tread ring, inner rim and hub, with a
// few tread ticks. Follows the lucide-react icon signature (currentColor
// stroke, 24 viewBox) so it drops into the same styling utilities.
export const Tyre: React.FC<React.SVGProps<SVGSVGElement>> = ({
  className,
  strokeWidth = 2,
  ...props
}) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    {...props}
  >
    <circle cx="12" cy="12" r="9.5" />
    <circle cx="12" cy="12" r="5.5" />
    <circle cx="12" cy="12" r="1.5" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" />
  </svg>
);

export {
  ShieldCheck,
  AlertTriangle,
  AlertCircle,
  Wrench,
  Gauge,
  CheckCircle2,
  ChevronDown,
  Loader2,
  ArrowRight,
  ArrowLeft,
  BrainCircuit,
  Database,
  Route,
  X,
  MapPin,
  Mail,
  Car,
  Check,
  Heart,
  Phone,
  User,
  Clock,
  Share2,
  Link2,
  MessageCircle,
  BellRing,
  CalendarDays,
  CarFront,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Lightbulb
};
