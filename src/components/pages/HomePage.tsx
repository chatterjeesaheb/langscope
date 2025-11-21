'use client';

import React, { useState, useEffect } from 'react';
import Layout from '@/components/layout/Layout';
import SearchBar from '@/components/ui/SearchBar';
import DomainCard from '@/components/domain/DomainCard';
import Button from '@/components/ui/Button';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useAuth } from '@/contexts/AuthContext';
import type { Domain } from '@/types';

const HomePage: React.FC = () => {
  const { user, isAuthenticated } = useAuth();
  const [domains, setDomains] = useState<Domain[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    fetchDomains();
  }, []);

  const fetchDomains = async () => {
    try {
      setLoading(true);
      
      // Simulate API delay
      await new Promise(resolve => setTimeout(resolve, 500));
      
      // Mock domains data
      const mockDomains: Domain[] = [
        {
          id: '1',
          name: 'Code Generation',
          slug: 'code-generation',
          description: 'Generate, debug, and optimize code across multiple programming languages',
          icon: '💻',
          battleCount: 15234,
          modelCount: 45,
          featured: true,
          color: '#3B82F6',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '2',
          name: 'Mathematical Reasoning',
          slug: 'mathematical-reasoning',
          description: 'Solve complex mathematical problems and explain step-by-step solutions',
          icon: '🔢',
          battleCount: 12456,
          modelCount: 38,
          featured: true,
          color: '#8B5CF6',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '3',
          name: 'Creative Writing',
          slug: 'creative-writing',
          description: 'Generate creative content including stories, poems, and scripts',
          icon: '✍️',
          battleCount: 10123,
          modelCount: 42,
          featured: true,
          color: '#EC4899',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '4',
          name: 'Data Analysis',
          slug: 'data-analysis',
          description: 'Analyze datasets, create visualizations, and derive insights',
          icon: '📊',
          battleCount: 8934,
          modelCount: 35,
          featured: false,
          color: '#10B981',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '5',
          name: 'Language Translation',
          slug: 'language-translation',
          description: 'Translate text accurately across multiple languages',
          icon: '🌍',
          battleCount: 9567,
          modelCount: 40,
          featured: false,
          color: '#F59E0B',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '6',
          name: 'Question Answering',
          slug: 'question-answering',
          description: 'Answer factual questions with accurate and relevant information',
          icon: '❓',
          battleCount: 11234,
          modelCount: 43,
          featured: false,
          color: '#06B6D4',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '7',
          name: 'Medical Assistance',
          slug: 'medical-assistance',
          description: 'Healthcare chatbots and medical information in regional languages',
          icon: '🏥',
          battleCount: 8456,
          modelCount: 32,
          featured: false,
          color: '#EF4444',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '8',
          name: 'Legal Document Analysis',
          slug: 'legal-analysis',
          description: 'Analyze legal documents and provide insights',
          icon: '⚖️',
          battleCount: 6234,
          modelCount: 28,
          featured: false,
          color: '#78350F',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '13',
          name: 'Finance',
          slug: 'finance',
          description: 'Financial analysis, forecasting, and market insights',
          icon: '💰',
          battleCount: 7234,
          modelCount: 31,
          featured: true,
          color: '#10B981',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '9',
          name: 'Educational Content',
          slug: 'education',
          description: 'Create and explain educational content for students',
          icon: '📚',
          battleCount: 9876,
          modelCount: 37,
          featured: false,
          color: '#7C3AED',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '10',
          name: 'Customer Support',
          slug: 'customer-support',
          description: 'Automated customer service and support conversations',
          icon: '💬',
          battleCount: 12345,
          modelCount: 41,
          featured: false,
          color: '#2563EB',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '11',
          name: 'Content Moderation',
          slug: 'content-moderation',
          description: 'Detect inappropriate content and enforce community guidelines',
          icon: '🛡️',
          battleCount: 7890,
          modelCount: 30,
          featured: false,
          color: '#DC2626',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
        {
          id: '12',
          name: 'Document Summarization',
          slug: 'summarization',
          description: 'Summarize long documents into concise summaries',
          icon: '📄',
          battleCount: 10567,
          modelCount: 39,
          featured: false,
          color: '#059669',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
      ];
      
      setDomains(mockDomains);
    } catch (error) {
      console.error('Failed to fetch domains:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = (query: string) => {
    // This is called by the debounced onSearch callback
    // The searchQuery state is already updated by onChange
    // So we don't need to update it here again
    // Only perform search action if there's a non-empty term
    if (query.trim()) {
      // Redirect to rankings with search or show filtered domains
      // For now, just filter domains. In future, could redirect to search results page
    }
  };

  const filteredDomains = domains.filter((d) =>
    d.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <Layout user={user} isAuthenticated={isAuthenticated}>
      {/* Hero Section */}
      <section className="text-center py-6 md:py-8 relative overflow-hidden">
        {/* Background decoration */}
        <div className="absolute inset-0 -z-10">
          <div className="absolute top-0 left-1/4 w-96 h-96 bg-slate-200/20 rounded-full blur-3xl"></div>
          <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-slate-300/20 rounded-full blur-3xl"></div>
        </div>
        
        <div className="max-w-4xl mx-auto relative z-10">
          <h1 className="text-3xl md:text-5xl font-extrabold bg-gradient-to-r from-gray-900 via-slate-700 to-slate-800 bg-clip-text text-transparent mb-5 leading-tight">
            Find the Perfect LLM for Your Use Case
          </h1>

          {/* Large Search Bar */}
          <div className="max-w-3xl mx-auto mb-4">
            <div className="relative">
              <SearchBar
                placeholder="Describe your use case..."
                onSearch={handleSearch}
                value={searchQuery}
                onChange={(value) => setSearchQuery(value)}
                size="lg"
              />
            </div>
            {/* Search Button */}
            <div className="mt-4 flex justify-center">
              <Button
                onClick={() => handleSearch(searchQuery)}
                variant="primary"
                size="lg"
                className="px-8"
              >
                Search
              </Button>
            </div>
          </div>

          {/* Example Text */}
          <p className="text-xs md:text-sm text-gray-500 mb-4 font-medium">
            Example: Medical diagnosis assistant in Hindi — Legal contract analysis — Customer support chatbot
          </p>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-6 bg-gradient-to-br from-white via-slate-50/30 to-slate-100/30 rounded-3xl border-2 border-slate-200/50 mb-6 shadow-xl shadow-slate-500/5 backdrop-blur-sm">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="flex items-start gap-3 p-4 rounded-2xl bg-white/60 backdrop-blur-sm border border-slate-200/50 hover:bg-white/80 hover:border-slate-300 hover:shadow-lg hover:shadow-slate-500/10 transition-all duration-300 group">
            <div className="flex-shrink-0 w-12 h-12 bg-slate-700 rounded-2xl flex items-center justify-center text-white text-xl shadow-lg group-hover:scale-110 group-hover:rotate-3 transition-all duration-300">
              📊
            </div>
            <div>
              <h3 className="font-bold text-gray-900 mb-1.5 text-sm">Data-Driven Rankings</h3>
              <p className="text-xs text-gray-600 leading-relaxed">from 10,000+ LLM Battles</p>
            </div>
          </div>
          
          <div className="flex items-start gap-3 p-4 rounded-2xl bg-white/60 backdrop-blur-sm border border-slate-200/50 hover:bg-white/80 hover:border-slate-300 hover:shadow-lg hover:shadow-slate-500/10 transition-all duration-300 group">
            <div className="flex-shrink-0 w-12 h-12 bg-slate-700 rounded-2xl flex items-center justify-center text-white text-xl shadow-lg group-hover:scale-110 group-hover:rotate-3 transition-all duration-300">
              🌐
            </div>
            <div>
              <h3 className="font-bold text-gray-900 mb-1.5 text-sm">Multi-Domain Support</h3>
              <p className="text-xs text-gray-600 leading-relaxed">Medical, Legal, Finance, +12 more</p>
            </div>
          </div>
          
          <div className="flex items-start gap-3 p-4 rounded-2xl bg-white/60 backdrop-blur-sm border border-slate-200/50 hover:bg-white/80 hover:border-slate-300 hover:shadow-lg hover:shadow-slate-500/10 transition-all duration-300 group">
            <div className="flex-shrink-0 w-12 h-12 bg-slate-700 rounded-2xl flex items-center justify-center text-white text-xl shadow-lg group-hover:scale-110 group-hover:rotate-3 transition-all duration-300">
              🔒
            </div>
            <div>
              <h3 className="font-bold text-gray-900 mb-1.5 text-sm">Immutable Public Ledger</h3>
              <p className="text-xs text-gray-600 leading-relaxed">No Gaming, No Collusion</p>
            </div>
          </div>
          
          <div className="flex items-start gap-3 p-4 rounded-2xl bg-white/60 backdrop-blur-sm border border-slate-200/50 hover:bg-white/80 hover:border-slate-300 hover:shadow-lg hover:shadow-slate-500/10 transition-all duration-300 group">
            <div className="flex-shrink-0 w-12 h-12 bg-slate-700 rounded-2xl flex items-center justify-center text-white text-xl shadow-lg group-hover:scale-110 group-hover:rotate-3 transition-all duration-300">
              ⚔️
            </div>
            <div>
              <h3 className="font-bold text-gray-900 mb-1.5 text-sm">Test Top Models Yourself</h3>
              <p className="text-xs text-gray-600 leading-relaxed">Manual Arena Mode</p>
            </div>
          </div>
        </div>
      </section>

      {/* Domain Showcase */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold bg-gradient-to-r from-gray-900 via-slate-700 to-gray-900 bg-clip-text text-transparent">
            Domain Showcase
            {searchQuery && ` (${filteredDomains.length} results)`}
          </h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {loading ? (
            <>
              {[...Array(12)].map((_, i) => (
                <SkeletonCard key={i} />
              ))}
            </>
          ) : filteredDomains.length > 0 ? (
            filteredDomains.map((domain) => (
              <DomainCard key={domain.id} domain={domain} featured={domain.featured} />
            ))
          ) : (
            <div className="col-span-full text-center py-12">
              <p className="text-gray-500">No domains found matching "{searchQuery}"</p>
            </div>
          )}
        </div>
      </section>

      {/* How It Works Section */}
      <section className="py-12 mt-12">
        <h2 className="text-2xl md:text-3xl font-extrabold bg-gradient-to-r from-gray-900 via-slate-700 to-gray-900 bg-clip-text text-transparent text-center mb-8">How It Works</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            {
              step: '1',
              title: 'Select Domain',
              description: 'Choose the domain you want to evaluate models in',
              icon: '🎯',
            },
            {
              step: '2',
              title: 'Battle Arena',
              description: 'Models compete in head-to-head battles with blind judging',
              icon: '⚔️',
            },
            {
              step: '3',
              title: 'Verified Rankings',
              description: 'Results are cryptographically verified for data integrity',
              icon: '🔒',
            },
          ].map((item) => (
            <div
              key={item.step}
              className="text-center p-6 bg-white/70 backdrop-blur-md rounded-3xl border-2 border-gray-200/50 shadow-lg hover:shadow-2xl hover:shadow-slate-500/10 hover:border-slate-300 hover:scale-[1.02] transition-all duration-300 group relative overflow-hidden"
            >
              <div className="absolute inset-0 bg-gradient-to-br from-slate-50/0 via-slate-50/0 to-slate-100/0 group-hover:from-slate-50/30 group-hover:via-slate-50/20 group-hover:to-slate-100/30 transition-all duration-300"></div>
              <div className="relative z-10">
                <div className="text-5xl mb-4 group-hover:scale-110 group-hover:rotate-6 transition-all duration-300">{item.icon}</div>
                <div className="inline-block px-4 py-1.5 bg-slate-700 text-white rounded-full text-xs font-bold mb-4 shadow-lg">
                  Step {item.step}
                </div>
                <h3 className="text-lg font-bold text-gray-900 mb-2">{item.title}</h3>
                <p className="text-sm text-gray-600 leading-relaxed">{item.description}</p>
              </div>
            </div>
          ))}
        </div>
      </section>
    </Layout>
  );
};

export default HomePage;

