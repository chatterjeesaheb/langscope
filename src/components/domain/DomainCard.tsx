import React from 'react';
import { cn } from '@/lib/utils';
import { formatNumber } from '@/lib/utils';
import Button from '@/components/ui/Button';
import type { Domain } from '@/types';

interface DomainCardProps {
  domain: Domain;
  featured?: boolean;
  className?: string;
}

const DomainCard: React.FC<DomainCardProps> = ({
  domain,
  featured = false,
  className,
}) => {
  // Get icon background color based on domain color or use default
  const iconBgColor = domain.color || '#475569'; // Default to slate-600
  
  // Card is not clickable - only the button navigates
  return (
    <div
      className={cn(
        'group relative bg-white rounded-xl border border-gray-200 p-5',
        'transition-all duration-300 hover:shadow-lg hover:border-gray-300',
        featured && 'ring-1 ring-slate-300',
        className
      )}
    >
      {featured && (
        <div className="absolute -top-2 -right-2 bg-slate-800 text-white px-2.5 py-0.5 rounded-full text-xs font-semibold shadow-md flex items-center gap-1">
          <span>⭐</span>
          <span>Featured</span>
        </div>
      )}

      <div className="flex flex-col h-full">
        {/* Icon */}
        <div 
          className="w-16 h-16 rounded-xl flex items-center justify-center text-3xl mb-4 shadow-md"
          style={{ backgroundColor: iconBgColor }}
        >
          {domain.icon}
        </div>

        {/* Content */}
        <div className="flex-1 flex flex-col">
          <h3 className="text-base font-bold text-gray-900 mb-1.5 group-hover:text-slate-700 transition-colors">
            {domain.name}
          </h3>
          <p className="text-sm text-gray-600 line-clamp-2 leading-relaxed mb-4 flex-1">{domain.description}</p>
          
          {/* Battle and model count */}
          <div className="mb-4 text-xs text-gray-600">
            <span className="font-semibold text-gray-900">{formatNumber(domain.battleCount)}</span>
            <span> battles • </span>
            <span className="font-semibold text-gray-900">{domain.modelCount || 0}</span>
            <span> models</span>
          </div>

          {/* Explore Rankings Button */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              window.location.href = `/rankings/${domain.slug}`;
            }}
            className="w-full rounded-lg font-medium hover:bg-slate-50 hover:border-slate-400 text-sm"
            iconPosition="right"
            icon={
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
              </svg>
            }
          >
            Explore Rankings
          </Button>
        </div>
      </div>
    </div>
  );
};

export default DomainCard;

